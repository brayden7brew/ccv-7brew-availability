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
    return {fields, dataset:{day:['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'][i]},
      querySelector(selector) { return fields[selector === '.day-error' ? 'error' : selector.match(/_(mode|start|end)/)[1]]; }};
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
  p.hours(0,'10:00','05:00'); assert.match(p.rows[0].fields.error.textContent,/later/);
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
