const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {runInNewContext} = require('node:vm');

test('rejection requires a nonblank reason while approval remains optional', () => {
  const handlers = {}, noteHandlers = {}, windowHandlers = {};
  const note = {value:'', addEventListener(name, fn) {noteHandlers[name]=fn;}, focus() {}};
  const reject = {}, help = {}, approve = {};
  const confirmation = {checked:false,addEventListener(name,fn) {noteHandlers.confirm=fn;}};
  const form = {addEventListener(name, fn) {handlers[name]=fn;}};
  const ids = {'review-form':form,'review-note':note,'reject-button':reject,'reject-help':help,'approve-button':approve,'replace-existing':confirmation};
  runInNewContext(readFileSync('app/static/review-form.js','utf8'), {
    document:{getElementById(id) {return ids[id];}},
    window:{addEventListener(name, fn) {windowHandlers[name]=fn;}}
  });
  const submit = (submitter) => {
    let prevented=false;
    handlers.submit({submitter,preventDefault(){prevented=true;}});
    return prevented;
  };
  assert.notEqual(approve.disabled,true); assert.equal(submit(approve),false);
  assert.equal(reject.disabled,true);
  assert.equal(submit(reject),true);
  assert.equal(submit({value:'approve'}),false);
  note.value=' \n\t'; noteHandlers.input(); assert.equal(reject.disabled,true);
  note.value='Please include weekend hours.'; noteHandlers.input();
  assert.equal(reject.disabled,false); assert.equal(submit(reject),false);
  note.value=''; noteHandlers.input(); assert.equal(reject.disabled,true);
  note.value='Restored reason'; windowHandlers.pageshow(); assert.equal(reject.disabled,false);
});
