import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';
/// <reference types="vitest" />

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  build: {
    target: 'es2020',
    rollupOptions: {
      output: {
        manualChunks: {
          vendor: ['react', 'react-dom', 'react-router-dom'],
          mui: ['@mui/material', '@mui/x-data-grid'],
          graph: ['react-force-graph-2d', 'cytoscape'],
          redux: ['@reduxjs/toolkit', 'react-redux'],
        },
      },
    },
  },
  worker: {
    format: 'es',
  },
  server: {
    port: 5333,
    proxy: {
      '/api': 'http://localhost:8333',
      '/ws': { target: 'ws://localhost:8333', ws: true },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
    css: false,
  },
});
