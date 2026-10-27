import {defineConfig} from '@playwright/test';
export default defineConfig({testDir: './e2e', workers: 1, retries: 0,
  use: {baseURL: 'http://localhost:5173', channel: 'chrome', viewport: {width: 1440, height: 1000}, trace: 'retain-on-failure'},
  reporter: [['list'], ['json', {outputFile: 'test-results/results.json'}]],
});
