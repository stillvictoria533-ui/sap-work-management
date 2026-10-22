import test from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID, createHash } from 'node:crypto';
import { createServer } from 'node:http';
import { serverOrigin, jobArguments, validateResult, childEnvironment, requestJson } from './connector.mjs';
const job = () => ({id:randomUUID(), system:'DS4', client:'110', operation:'tcode', object_name:'SE38', expires_at:new Date(Date.now()+240_000).toISOString(), lease:'a'.repeat(43)});
test('only explicit TLS or local loopback server origins', () => {
  assert.equal(serverOrigin('http://127.0.0.1:5175'), 'http://127.0.0.1:5175');
  assert.equal(serverOrigin('https://43.130.36.105'), 'https://43.130.36.105');
  for (const url of ['http://10.2.22.34', 'https://x@y', 'https://x/a', 'file:///etc/passwd', 'https://x/?token=a']) assert.throws(() => serverOrigin(url));
});
test('fixed argv rejects system/command/endpoint injection and expired jobs', () => {
  assert.deepEqual(jobArguments(job()).slice(1), ['tcode','SE38','--conn','DS4']);
  for (const change of [{system:'PS4'}, {system:'QS4'}, {client:'100'}, {operation:'data'}, {object_name:'SE38;id'}, {object_name:'--help'},
    {object_name:'*'}, {endpoint:'10.2.22.40'}, {args:['--conn','PS4']}, {expires_at:'bad'}, {expires_at:'2020-01-01'}, {id:'../../x'}]) {
    assert.throws(() => jobArguments({...job(), ...change}));
  }
});
test('SAP subprocess excludes model, device and inherited SAP passwords', () => {
  assert.deepEqual(childEnvironment({HOME:'/home/test', PATH:'/bin', OPENAI_API_KEY:'secret', SAP_PASSWORD:'secret', NODE_OPTIONS:'--import bad', DEVICE_TOKEN:'secret'}), {HOME:'/home/test', PATH:'/bin'});
});
test('result must match the exact non-authoritative DS4 query', () => {
  const j = job();
  const hash = createHash('sha256').update(JSON.stringify({tcode:'SE38'})).digest('hex');
  const result = {tcode:'SE38', _evidence:{trust:'UNTRUSTED_EVIDENCE', instructionAuthority:false, system:'DS4', client:'110', command:'tcode', object:'SE38', contentSha256:hash}};
  assert.deepEqual(validateResult(result,j), result);
  assert.equal(validateResult({...result,_evidence:{...result._evidence,user:'PRIVATE_LOGIN'}},j)._evidence.user, undefined);
  assert.throws(() => validateResult({...result, changed:true},j));
  for (const change of [{system:'PS4'}, {client:'100'}, {object:'SE11'}, {instructionAuthority:true}, {contentSha256:''}]) {
    assert.throws(() => validateResult({...result,_evidence:{...result._evidence,...change}},j));
  }
  assert.throws(() => validateResult({...result,text:'x'.repeat(100_000)},j));
});
test('device credentials never follow HTTP redirects', async () => {
  let destinationHits = 0;
  const server = createServer((req,res) => {
    if (req.url === '/leak') { destinationHits++; res.end('{}'); }
    else { res.writeHead(302,{Location:'/leak'}); res.end(); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try { await assert.rejects(requestJson(`http://127.0.0.1:${server.address().port}`, 'agent/claim', {}, 'secret')); assert.equal(destinationHits,0); }
  finally { await new Promise(resolve=>server.close(resolve)); }
});
