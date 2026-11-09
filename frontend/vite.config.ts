import {defineConfig} from 'vite'
import vue from '@vitejs/plugin-vue'
export default defineConfig({plugins:[vue()],define:{__STARBRIDGE_NATIVE__:'true'},esbuild:{jsx:'automatic'},resolve:{dedupe:['react','react-dom','@tanstack/react-query','@tanstack/react-virtual','radix-ui','lucide-react']},build:{outDir:'../webui',emptyOutDir:true,sourcemap:false},server:{port:18861,proxy:{'/api':'http://127.0.0.1:18970','/registration':'http://127.0.0.1:18970'}}})
