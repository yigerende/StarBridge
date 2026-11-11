import {defineConfig} from 'vite'
import vue from '@vitejs/plugin-vue'
import {fileURLToPath} from 'node:url'
export default defineConfig({plugins:[vue()],define:{__STARBRIDGE_NATIVE__:'true'},esbuild:{jsx:'automatic'},resolve:{alias:[{find:/^\.\/TokenCredentials$/,replacement:fileURLToPath(new URL('./src/registration/SessionCredentials.tsx',import.meta.url))}],dedupe:['react','react-dom','@tanstack/react-query','@tanstack/react-virtual','radix-ui','lucide-react']},build:{outDir:'../webui',emptyOutDir:true,sourcemap:false},server:{port:18861,proxy:{'/api':'http://127.0.0.1:18970','/registration':'http://127.0.0.1:18970'}}})
