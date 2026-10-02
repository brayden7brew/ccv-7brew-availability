const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {runInNewContext} = require('node:vm');
const source = readFileSync('app/static/weekly-form.js', 'utf8');

function element(value = '') {
  return {value, textContent: '', disabled: false, hidden: false, attributes: {},
    setAttribute(key, value) { this.attributes[key] = value; }};
}
function page(minimum = 900, blocked = false) {
  const date = Object.assign(element(), {min:'2026-10-15', max:'2027-10-02', validity:{valid:true}});
  const note = element();
  const button = element(); button.textContent = 'Send to my manager';
  const credit = element(), feedback = element(), events = {}, windowEvents = {};
  const rows = Array.from({length:7}, (_,i) => {
    const fields = {mode:element('none'),start:element(),end:element(),error:element()};
    fields.end.options = [element(), ...Array.from({length:73}, (_,n) => {
      const minutes=300+n*15;
      return element(`${String(Math.floor(minutes/60)).padStart(2,'0')}:${String(minutes%60).padStart(2,'0')}`);
    })];
    const period = {setAttribute() {}, querySelector(selector) { return selector === '.remove-period' ? element() : fields[selector.match(/_(start|end)/)[1]]; }, querySelectorAll() { return [fields.start, fields.end]; }};
    return {fields, periods:[period], querySelectorAll() { return this.periods; }, dataset:{day:['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'][i]},
      querySelector(selector) { if (selector === '.add-period') return element(); return fields[selector === '.day-error' ? 'error' : selector.match(/_(mode|start|end)/)[1]]; }};
  });
  const form = {dataset:{minimumMinutes:String(minimum),limitBlocked:String(blocked)},
    elements:{namedItem(name) { return name === 'effective_date' ? date : note; }},
    querySelectorAll() { return rows; }, addEventListener(event, handler) { events[event] = handler; }};
  const ids = {'weekly-form':form,'weekly-submit':button,'weekly-credit':credit,'weekly-errors':feedback};
  runInNewContext(source, {document:{getElementById(id) { return ids[id]; }},
    window:{addEventListener(event, handler) { windowEvents[event] = handler; }}});
  return {date,note,button,credit,feedback,rows,change:events.change, input:events.input, restore:windowEvents.pageshow,
    submit() { let prevented=false; events.submit({preventDefault(){prevented=true;}}); return prevented; },
    hours(i,start,end) { rows[i].fields.mode.value='hours'; rows[i].fields.start.value=start; rows[i].fields.end.value=end; events.change(); }};
}

test('empty form is blocked and one long day cannot meet a 15-hour minimum', () => {
  const p=page(); assert.equal(p.button.disabled,true); assert.equal(p.submit(),true);
  p.date.value=p.date.min; p.hours(0,'05:00','23:00');
  assert.match(p.credit.textContent,/10 of 15/); assert.equal(p.button.disabled,true);
  p.hours(1,'05:00','10:00'); assert.equal(p.button.disabled,false);
  assert.match(p.credit.textContent,/15 of 15/);
  assert.equal(p.submit(),false); assert.equal(p.button.disabled,true); assert.equal(p.submit(),true);
  p.restore(); assert.equal(p.button.disabled,false);
});

test('all-day credit is capped and changing to no hours removes credit', () => {
  const p=page(); p.date.value=p.date.min;
  p.rows[0].fields.mode.value='all_day'; p.change();
  assert.match(p.credit.textContent,/10 of 15/); assert.equal(p.button.disabled,true);
  assert.equal(p.rows[0].fields.start.disabled,true);
  p.rows[1].fields.mode.value='all_day'; p.change(); assert.equal(p.button.disabled,false);
  p.rows[0].fields.mode.value='none'; p.change(); assert.equal(p.button.disabled,true);
});

test('time errors remain inline and minimum-off does not disable time validation', () => {
  const p=page(0); p.date.value=p.date.min; p.change(); assert.equal(p.button.disabled,false);
  p.hours(0,'05:00',''); assert.equal(p.button.disabled,true);
  assert.match(p.rows[0].fields.error.textContent,/both/);
  assert.equal(p.rows[0].fields.start.required,true);
  p.hours(0,'10:00','05:00'); assert.equal(p.rows[0].fields.end.value,''); assert.equal(p.button.disabled,true);
  p.hours(0,'05:01','10:00'); assert.equal(p.button.disabled,true);
  p.hours(0,'04:45','10:00'); assert.equal(p.button.disabled,true);
  p.hours(0,'05:00','10:00'); assert.equal(p.button.disabled,false);
});

test('notice dates, new-hire dates, notes and request limits control submission', () => {
  const p=page(0); p.date.value='2026-10-14'; p.change(); assert.equal(p.button.disabled,true);
  p.date.value=p.date.min; p.change(); assert.equal(p.button.disabled,false);
  p.date.value='2027-10-03'; p.change(); assert.equal(p.button.disabled,true);
  p.date.min='2026-10-02'; p.date.value='2026-10-02'; p.change(); assert.equal(p.button.disabled,false);
  p.note.value='x'.repeat(2001); p.input(); assert.equal(p.button.disabled,true);
  const blocked=page(0,true); blocked.date.value=blocked.date.min; blocked.change();
  assert.equal(blocked.button.disabled,true); assert.equal(blocked.submit(),true);
});


test('To choices must follow From and reset when From catches up', () => {
  const p=page(0); p.date.value=p.date.min;
  const row=p.rows[0].fields;
  row.mode.value='hours'; p.change();
  assert.equal(row.end.disabled,true);
  p.hours(0,'10:00','11:00');
  assert.equal(row.end.disabled,false);
  assert.equal(row.end.options.find(o=>o.value==='10:00').disabled,true);
  assert.equal(row.end.options.find(o=>o.value==='09:45').disabled,true);
  assert.equal(row.end.options.find(o=>o.value==='10:15').disabled,false);
  row.start.value='11:00'; p.change();
  assert.equal(row.end.value,''); assert.equal(p.button.disabled,true);
  row.start.value='05:00'; p.change();
  assert.equal(row.end.options.find(o=>o.value==='10:00').disabled,false);
  p.hours(0,'22:45','23:00'); assert.equal(p.button.disabled,false);
  row.start.value='23:00'; p.change();
  assert.equal(row.end.value,'');
  assert.equal(row.end.options.filter(o=>o.value && !o.disabled).length,0);
});

test('unavailable hours count the remaining operating hours with the daily cap', () => {
  const p=page(); p.date.value=p.date.min;
  const row=p.rows[0].fields;
  row.mode.value='unavailable'; row.start.value='05:00'; row.end.value='23:00'; p.change();
  assert.match(p.credit.textContent,/0 of 15/); assert.equal(p.button.disabled,true);
  row.end.value='18:00'; p.change();
  assert.match(p.credit.textContent,/5 of 15/);
  p.rows[1].fields.mode.value='all_day'; p.change();
  assert.match(p.credit.textContent,/15 of 15/); assert.equal(p.button.disabled,false);
  row.start.value='10:00'; p.change();
  assert.match(p.credit.textContent,/20 of 15/);
  row.end.value=''; p.change(); assert.equal(p.button.disabled,true);
});

 test('split unavailable periods subtract every period and reject overlap', () => {
  const p=page(0); p.date.value=p.date.min;
  const row=p.rows[0]; row.fields.mode.value='unavailable';
  row.fields.start.value='05:00'; row.fields.end.value='09:30';
  const start=element('17:00'), end=element('22:00'); end.options=row.fields.end.options;
  row.periods.push({setAttribute() {}, querySelector(s) { return s === '.remove-period' ? element() : s.includes('_start') ? start : end; }, querySelectorAll() { return [start,end]; }});
  p.change(); assert.match(p.credit.textContent,/8.5 hours counted/); assert.equal(p.button.disabled,false);
  start.value='09:15'; p.change(); assert.equal(p.button.disabled,true); assert.match(row.fields.error.textContent,/overlap/);
  start.value='09:30'; p.change(); assert.equal(p.button.disabled,false); assert.match(p.credit.textContent,/1 hours counted/);
});
