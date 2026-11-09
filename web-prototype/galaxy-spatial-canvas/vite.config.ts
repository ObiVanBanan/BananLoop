import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';
// Local only; loadEnv picks up BANANLOOP_API_URL from the launch environment.
export default defineConfig(({mode})=>{
 const env=loadEnv(mode,'.','');
 return {
  plugins:[react()],
  server:{
   host:'127.0.0.1',port:5173,strictPort:true,
   proxy:{'/api':{target:env.BANANLOOP_API_URL||'http://127.0.0.1:8765',
    rewrite:(path:string)=>path.replace(/^\/api/,'')}}
  }
 };
});
