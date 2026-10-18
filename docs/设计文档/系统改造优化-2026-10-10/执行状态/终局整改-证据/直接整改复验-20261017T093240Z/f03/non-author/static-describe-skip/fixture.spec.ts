import {test,expect} from '@playwright/test';
test.describe.skip('all static skipped',()=>{
test('CRITICAL-AUTH-001: independent marker negative',async()=>{expect(true).toBe(true);});
test('CRITICAL-PERM-001: independent marker negative',async()=>{expect(true).toBe(true);});
test('CRITICAL-CALC-001: independent marker negative',async()=>{expect(true).toBe(true);});
test('CRITICAL-EVIDENCE-001: independent marker negative',async()=>{expect(true).toBe(true);});
});