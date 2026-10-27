import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// One frontend. P5 owns the future general app shell/router.
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1', port: 5173, strictPort: true,
    proxy: {'/api': {target: 'http://127.0.0.1:8000', changeOrigin: false}},
  },
});
