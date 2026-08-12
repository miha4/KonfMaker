const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const ROOT_DIR = path.resolve(__dirname, '..');
const RELEASE_DIR = path.join(ROOT_DIR, 'release');
const SETUP_NAME = 'ATCConfMakerSetup.exe';

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: ROOT_DIR,
    env: { ...process.env, ...(options.env || {}) },
    stdio: 'inherit',
    shell: false,
  });
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    throw new Error(`${command} ${args.join(' ')} failed with exit code ${result.status}`);
  }
}

function removePreviousUnpackedBuilds() {
  if (!fs.existsSync(RELEASE_DIR)) {
    return;
  }
  for (const entry of fs.readdirSync(RELEASE_DIR, { withFileTypes: true })) {
    if (entry.isDirectory() && entry.name.endsWith('-unpacked')) {
      fs.rmSync(path.join(RELEASE_DIR, entry.name), { recursive: true, force: true });
    }
  }
}

function removePreviousSetup() {
  fs.rmSync(path.join(RELEASE_DIR, SETUP_NAME), { force: true });
}

function main() {
  if (process.platform !== 'win32') {
    throw new Error('ATCConfMakerSetup.exe se mora zgraditi na Windows računalniku ali Windows CI okolju.');
  }

  run(process.execPath, [path.join(ROOT_DIR, 'scripts', 'build-backend-sidecar.cjs')], {
    env: { BACKEND_DESKTOP_BUNDLE_MODE: 'onedir' },
  });
  run(process.execPath, [path.join(ROOT_DIR, 'scripts', 'build-frontend-desktop.cjs')]);
  removePreviousUnpackedBuilds();
  removePreviousSetup();

  run(process.execPath, [
    path.join(ROOT_DIR, 'node_modules', 'electron-builder', 'out', 'cli', 'cli.js'),
    '--win',
    'nsis',
    '--x64',
    '--publish',
    'never',
    '--config.nsis.artifactName=ATCConfMakerSetup.exe',
    '--config.nsis.oneClick=true',
    '--config.nsis.perMachine=false',
    '--config.nsis.packElevateHelper=false',
  ]);

  const setupPath = path.join(RELEASE_DIR, SETUP_NAME);
  if (!fs.existsSync(setupPath)) {
    throw new Error(`${SETUP_NAME} ni bil ustvarjen.`);
  }
}

try {
  main();
} catch (error) {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
}
