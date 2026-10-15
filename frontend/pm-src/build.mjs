import { build } from 'esbuild';
import { mkdir, copyFile } from 'node:fs/promises';
const out = new URL('../dist/pm/', import.meta.url);
await mkdir(out, { recursive: true });
await build({entryPoints:[new URL('app.js', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/,'$1')], bundle:true, outdir:out.pathname.replace(/^\/([A-Za-z]:)/,'$1'), minify:false, target:'es2020'});
await copyFile(new URL('index.html', import.meta.url), new URL('index.html', out));
await copyFile(new URL('style.css', import.meta.url), new URL('style.css', out));
