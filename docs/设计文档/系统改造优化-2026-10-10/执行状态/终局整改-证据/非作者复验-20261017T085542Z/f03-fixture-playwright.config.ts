import { defineConfig } from '@playwright/test';
export default defineConfig({testDir:'./tests',workers:1,fullyParallel:false,retries:0,timeout:5000,reporter:'list',outputDir:'./test-results',projects:[{name:'chromium'}]});
