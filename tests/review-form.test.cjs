const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {runInNewContext} = require('node:vm');
function page() {
  const handlers = {}, noteHandlers = {}, windowHandlers = {}, children = [];
  const note = {value:'', addEventListener(name, fn) {noteHandlers[name]=fn;}, focus() {}};
  const reject = {value:'reject',textContent:'Reject'}, help = {}, approve = {value:'approve',textContent:'Approve weekly hours'};
  const form = {addEventListener(name, fn) {handlers[name]=fn;}, appendChild(c) {children.push(c);},setAttribute(){},removeAttribute(){}};
  const ids = {'review-form':form,'review-note':note,'reject-button':reject,'reject-help':help,'approve-button':approve};
  runInNewContext(readFileSync('app/static/review-form.js','utf8'), {
    document:{getElementById(id) {return ids[id];},createElement(){return {remove(){children.splice(children.indexOf(this),1);}};}},
    window:{addEventListener(name, fn) {windowHandlers[name]=fn;}}
  });
  return {note,reject,approve,children,change:()=>noteHandlers.input(),restore:()=>windowHandlers.pageshow(),submit(submitter) {
    let prevented=false;
    handlers.submit({submitter,preventDefault(){prevented=true;}});
    return prevented;
  }};
}
test('rejection requires a nonblank reason while approval remains optional', () => {
  const p=page();
  assert.equal(p.reject.disabled,true);
  assert.equal(p.submit(p.reject),true);
  p.note.value=' \n\t'; p.change(); assert.equal(p.reject.disabled,true);
  p.note.value='Please include weekend hours.'; p.change();
  assert.equal(p.reject.disabled,false); assert.equal(p.submit(p.reject),false);
  assert.equal(p.children[0].value,'reject');
  const q=page(); assert.equal(q.submit(q.approve),false);
  assert.equal(q.children[0].value,'approve');
});
test('repeat decisions are blocked and back navigation restores the form', () => {
  const p=page();
  assert.equal(p.submit(p.approve),false);
  assert.equal(p.approve.disabled,true); assert.equal(p.reject.disabled,true);
  assert.equal(p.submit(p.approve),true);
  p.note.value='New reason';p.change();assert.equal(p.reject.disabled,true);
  assert.equal(p.children.length,1);
  p.restore();
  assert.equal(p.children.length,0); assert.equal(p.approve.disabled,false);
  assert.equal(p.approve.textContent,'Approve weekly hours');
  assert.equal(p.reject.disabled,false);assert.equal(p.submit(p.reject),false);
  assert.equal(p.children[0].value,'reject');
});
