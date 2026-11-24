#!/usr/bin/env node
// Produces a separately downloadable runtime. Never copied into the Gateway payload.
import { copyFileSync, chmodSync, realpathSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync, createReadStream, statSync } from 'node:fs';
import { join, resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { pipeline } from 'node:stream/promises';
import * as tar from 'tar';

const VERSION='1.0.0', COMMIT='f696c556828e9b3ea6695cb4ac41ff233ba5f430';
function copyTree(source,destination,ancestors=new Set()) {
  const actual=realpathSync(source),info=statSync(actual);
  if(info.isDirectory()){
    if(ancestors.has(actual))throw new Error(`Cyclic runtime source: ${source}`);
    const visited=new Set([...ancestors,actual]);mkdirSync(destination,{recursive:true});
    for(const name of readdirSync(actual))copyTree(join(actual,name),join(destination,name),visited);
  }else if(info.isFile()){
    mkdirSync(dirname(destination),{recursive:true});copyFileSync(actual,destination);chmodSync(destination,info.mode&0o777);
  }else throw new Error(`Unsupported runtime entry: ${source}`);
}
const SOURCE_SHA256='cd0eaaebe74a75ad4371206bfde93c645495050ca3bc353cddec2ee597a36203';
const args=process.argv.slice(2);
const option=name=>{const index=args.indexOf(name);if(index<0||!args[index+1])throw new Error(`Required: ${name}`);return resolve(args[index+1]);};
const archive=option('--source-archive'), python=option('--python'), dotnet=option('--dotnet'), output=option('--output');
const project=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const target=`${process.platform}-${process.arch}`;
if(!['darwin-arm64','darwin-x64','win32-x64','linux-x64'].includes(target))throw new Error('Unsupported native build target');
const sha=async path=>{const hash=createHash('sha256');await pipeline(createReadStream(path),hash);return hash.digest('hex');};
if(await sha(archive)!==SOURCE_SHA256)throw new Error('Source archive must match the pinned official LEAN commit');
const info=JSON.parse(execFileSync(python,['-c','import sys,sysconfig,json,pandas,wrapt; print(json.dumps({"version":sys.version.split()[0],"base":sys.base_prefix,"site":sysconfig.get_paths()["purelib"],"pandas":pandas.__version__,"wrapt":wrapt.__version__}))'],{encoding:'utf8'}));
if(info.version!=='3.11.11'||info.pandas!=='2.2.3'||info.wrapt!=='1.16.0')throw new Error('Requires Python 3.11.11, pandas 2.2.3 and wrapt 1.16.0');
mkdirSync(output,{recursive:true});
const stage=join(output,`stage-${target}`);
if(existsSync(stage))throw new Error(`Build staging exists: ${stage}; preserve it or use a fresh output directory`);
mkdirSync(stage);
await tar.x({file:archive,cwd:stage,strip:1,strict:true});
const initializer=join(stage,'Common/Python/PythonInitializer.cs');
const source=readFileSync(initializer,'utf8');
const leaking='                    var pyLock = Py.GIL();';
if(source.split(leaking).length!==2)throw new Error('Upstream shutdown patch no longer applies');
writeFileSync(initializer,source.replace(leaking,'                    // Gateway patch 1: PythonEngine.Shutdown owns its GIL lifetime.'));
execFileSync(dotnet,['build','Launcher/QuantConnect.Lean.Launcher.csproj','-c','Release','--nologo','-p:UseSharedCompilation=false','-p:RestoreForce=true'],{stdio:'inherit',cwd:realpathSync(stage)});
const root=join(output,'lean-runtime');
if(existsSync(root))throw new Error(`Runtime output exists: ${root}`);
mkdirSync(root);
copyTree(join(stage,'Launcher/bin/Release'),join(root,'engine'));
const extensionOutput=join(stage,'gateway-host');
execFileSync(dotnet,['build',join(project,'src/quant/lean-host/Gateway.Quant.csproj'),'-c','Release',`-p:LeanRoot=${join(root,'engine')}`,'-o',extensionOutput,'--nologo'],{stdio:'inherit'});
copyTree(join(extensionOutput,'Gateway.Quant.dll'),join(root,'engine/Gateway.Quant.dll'));
// Copy only the runtime, not the SDK. Portable Python is supplied by the release
// builder (python-build-standalone); dependencies are copied from the checked env.
const dotnetRoot=dirname(realpathSync(dotnet));
for(const name of ['dotnet'+(process.platform==='win32'?'.exe':''),'host','shared','LICENSE.txt','ThirdPartyNotices.txt']){
  if(existsSync(join(dotnetRoot,name)))copyTree(join(dotnetRoot,name),join(root,'dotnet',name));
}
copyTree(info.base,join(root,'python'));
const site=process.platform==='win32'?'Lib/site-packages':'lib/python3.11/site-packages';
copyTree(info.site,join(root,'python',site));
for(const name of ['market-hours','symbol-properties'])copyTree(join(stage,'Data',name),join(root,'data',name));
copyTree(join(stage,'LICENSE'),join(root,'LEAN-LICENSE'));
copyTree(join(project,'src/quant/bridge'),join(root,'bridge'));
if(existsSync(join(project,'DEPENDENCIES.md')))copyTree(join(project,'DEPENDENCIES.md'),join(root,'DEPENDENCIES.md'));
writeFileSync(join(root,'LEAN-PATCHES.txt'),`Source: https://github.com/QuantConnect/Lean/tree/${COMMIT}\nPatch 1 removes an undisposed Py.GIL() handle in PythonInitializer.Shutdown; PythonEngine.Shutdown owns the GIL.\n`);
const pythonEntry=process.platform==='win32'?'python/python.exe':'python/bin/python3';
const pythonLibrary=process.platform==='win32'?'python/python311.dll':process.platform==='darwin'?'python/lib/libpython3.11.dylib':'python/lib/libpython3.11.so.1.0';
writeFileSync(join(root,'runtime.json'),JSON.stringify({version:VERSION,target,sourceCommit:COMMIT,shutdownPatch:1,python:pythonEntry,pythonHome:'python',pythonLibrary,dotnet:'dotnet/dotnet'+(process.platform==='win32'?'.exe':'')},null,2));
// Remove developer caches; preserve bundled license/notice files.
const clean=directory=>{for(const entry of readdirSync(directory,{withFileTypes:true})){const path=join(directory,entry.name);if(entry.isDirectory()){if(entry.name==='__pycache__')rmSync(path,{recursive:true});else clean(path);}}};
clean(root);
const filename=`lean-${VERSION}-${target}.tar.gz`, destination=join(output,filename);
await tar.c({cwd:output,file:destination,gzip:true,portable:true},['lean-runtime']);
writeFileSync(join(output,`${target}.json`),JSON.stringify({version:VERSION,target,sourceCommit:COMMIT,filename,size:statSync(destination).size,sha256:await sha(destination)},null,2));
console.log(`Prepared ${destination}. Run native smoke tests, then publish the archive and ${target}.json together. Gateway payload remains unchanged.`);
