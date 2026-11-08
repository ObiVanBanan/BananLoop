import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
// Only this local dev-server forwards browser reads to the existing loopback API.
export default defineConfig({
 plugins:[react()],server:{host:'127.0.0.1',port:5173,strictPort:true,
  proxy:{'/api':{target:process.env.BANANLOOP_API_URL||'http://127.0.0.1:8765',
   rewrite:(path:string)=>path.replace(/^\/api/,'')}}}
});