const {test}=require('node:test');
const assert=require('node:assert/strict');
const {readFileSync}=require('node:fs');
const {runInNewContext}=require('node:vm');
test('request status refreshes only processing lists and pauses in hidden tabs',()=>{
  for (const processing of [false,true]) {
    let reloads=0,callback=null;
    const document={visibilityState:'hidden',querySelector:()=>processing};
    runInNewContext(readFileSync('app/static/request-status.js','utf8'),{
      document,window:{setTimeout(fn,ms){assert.equal(ms,5000);callback=fn;},location:{reload(){reloads++;}}}
    });
    if (!processing) {assert.equal(callback,null);continue;}
    callback();assert.equal(reloads,0);
    document.visibilityState='visible';callback();assert.equal(reloads,1);
  }
});
