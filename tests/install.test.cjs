const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {runInNewContext} = require('node:vm');
const source = readFileSync('app/static/install.js', 'utf8');

function page(userAgent = 'iPhone', {standalone = false, dismissed = false, brokenStorage = false} = {}) {
  const ids = Object.fromEntries(['install-panel','install-help','install-app','install-steps','install-dismiss'].map(id => [id, {
    hidden:true, events:{}, focus(){}, addEventListener(name, fn){this.events[name] = fn;}
  }]));
  const events = {}, storage = new Map(dismissed ? [['ccv-install-dismissed-until', String(Date.now()+86400000)]] : []);
  runInNewContext(source, {
    document:{getElementById(id){return ids[id];}}, navigator:{userAgent},
    window:{matchMedia(){return {matches:standalone, addEventListener(){}};}, addEventListener(name, fn){events[name]=fn;}},
    localStorage:{getItem(key){if(brokenStorage) throw Error(); return storage.get(key);},setItem(key,value){if(brokenStorage) throw Error(); storage.set(key,value);}}
  });
  return {ids, events, storage};
}

test('iPhone explains manual installation, dismisses, and can reopen', () => {
  const {ids,storage}=page();
  assert.equal(ids['install-panel'].hidden,false);
  assert.match(ids['install-steps'].textContent,/In Safari, tap Share/);
  assert.equal(ids['install-app'].hidden,true);
  ids['install-dismiss'].events.click();
  assert.equal(ids['install-panel'].hidden,true);
  assert.ok(Number(storage.get('ccv-install-dismissed-until')) > Date.now()+29*86400000);
  ids['install-help'].events.click();
  assert.equal(ids['install-panel'].hidden,false);
});

test('desktop, installed apps, and dismissed reminders do not pop up', () => {
  for(const p of [page('Desktop'),page('iPhone',{standalone:true}),page('iPhone',{dismissed:true})]) {
    assert.equal(p.ids['install-panel'].hidden,true);
  }
  assert.equal(page('iPhone',{standalone:true}).ids['install-help'].hidden,true);
  assert.equal(page('iPhone',{brokenStorage:true}).ids['install-panel'].hidden,false);
});

test('Android offers native installation only after browser support is confirmed', async () => {
  const {ids,events}=page('Android');
  assert.equal(ids['install-app'].hidden,true);
  let prompted=false, prevented=false;
  events.beforeinstallprompt({preventDefault(){prevented=true;}, async prompt(){prompted=true;},userChoice:Promise.resolve({outcome:'accepted'})});
  assert.equal(prevented,true);
  assert.equal(ids['install-app'].hidden,false);
  await ids['install-app'].events.click();
  assert.equal(prompted,true);
  assert.equal(ids['install-panel'].hidden,true);
  events.appinstalled();
  assert.equal(ids['install-help'].hidden,true);
});

// The native app already has an icon; never ask it to install itself.
test('native CCV app suppresses Home Screen instructions', () => {
  const {ids}=page('iPhone CCVPortalApp/1.0');
  assert.equal(ids['install-panel'].hidden,true);
  assert.equal(ids['install-help'].hidden,true);
});
