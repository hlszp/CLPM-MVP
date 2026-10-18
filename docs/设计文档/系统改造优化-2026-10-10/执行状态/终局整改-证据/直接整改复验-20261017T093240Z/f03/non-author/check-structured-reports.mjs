import assert from 'node:assert/strict';
import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { join, dirname } from 'node:path';
import { validate } from '/Users/zhangping/DEV/clpm-remediation-20261017/e2e/scripts/validate-critical-results.mjs';

const out=dirname(fileURLToPath(import.meta.url));
const manifest=JSON.parse(readFileSync('/Users/zhangping/DEV/clpm-remediation-20261017/e2e/critical-cases.json','utf8'));
const input=JSON.parse(readFileSync(join(out,'contract-independent/passed/executed.json'),'utf8'));
const specs=(report)=>{const items=[];const walk=(s)=>{items.push(...(s.specs??[]));for(const child of s.suites??[])walk(child);};walk(report);return items;};
const cases=[
  ['normal',true,()=>{}],
  ['wrong-project',false,(report)=>{specs(report)[0].tests[0].projectName='unregistered';}],
  ['wrong-file',false,(report)=>{specs(report)[0].file='other.spec.ts';}],
  ['unexpected-outcome',false,(report)=>{specs(report)[0].tests[0].status='unexpected';}],
  ['expected-failure',false,(report)=>{specs(report)[0].tests[0].expectedStatus='failed';}],
  ['missing-stats',false,(report)=>{delete report.stats;}],
  ['mismatched-denominator',false,(report)=>{report.stats.expected=3;}],
  ['duplicate-attempt',false,(report)=>{const test=specs(report)[0].tests[0];test.results.push(structuredClone(test.results[0]));}],
  ['missing-attempt',false,(report)=>{specs(report)[0].tests[0].results=[];}],
  ['retry-index-nonzero',false,(report)=>{specs(report)[0].tests[0].results[0].retry=1;}],
  ['top-level-errors',false,(report)=>{report.errors=[{message:'report failure'}];}],
];
const results=[];
for(const [name,expected,mutate] of cases){
  const report=structuredClone(input);mutate(report);
  const result=validate('run',manifest,report);
  results.push({name,expected,...result});
  assert.equal(result.passed,expected,name);
}
writeFileSync(join(out,'structured-reports-results.json'),JSON.stringify(results,null,2)+'\n');
console.log(JSON.stringify({cases:results.length,passed:results.length,failed:0}));
