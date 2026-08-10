const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const ROOT_DIR = path.resolve(__dirname, '..');
const BACKEND_DIR = path.join(ROOT_DIR, 'backend');
const VENV_DIR = process.env.BACKEND_DESKTOP_BUNDLE_VENV_DIR || path.join(BACKEND_DIR, '.desktop-venv');
const isWindows = process.platform === 'win32';

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: options.cwd || ROOT_DIR,
    env: { ...process.env, ...(options.env || {}) },
    encoding: 'utf8',
    stdio: options.capture ? 'pipe' : 'inherit',
    shell: false,
  });

  if (result.error) {
    if (options.allowFailure) {
      return result;
    }
    throw result.error;
  }

  if (result.status !== 0 && !options.allowFailure) {
    throw new Error(`${command} ${args.join(' ')} failed with exit code ${result.status}`);
  }

  return result;
}

function pathExists(candidate) {
  try {
    return fs.existsSync(candidate);
  } catch {
    return false;
  }
}

function pythonVersion(command, args = []) {
  const result = spawnSync(command, [
    ...args,
    '-c',
    'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")',
  ], {
    encoding: 'utf8',
    stdio: 'pipe',
    shell: false,
  });

  if (result.status !== 0) {
    return null;
  }

  const text = result.stdout.trim();
  const parts = text.split('.').map((part) => Number(part));
  if (parts.length < 2 || parts.some((part) => Number.isNaN(part))) {
    return null;
  }

  return { text, major: parts[0], minor: parts[1] };
}

function isSupportedPython(version) {
  return version && (version.major > 3 || (version.major === 3 && version.minor >= 10));
}

function splitCommand(command) {
  if (Array.isArray(command)) {
    return command;
  }
  return [command];
}

function findPython() {
  const candidates = [];

  if (process.env.PYTHON) {
    candidates.push(splitCommand(process.env.PYTHON));
  }

  candidates.push([path.join(BACKEND_DIR, '.venv', isWindows ? 'Scripts\\python.exe' : 'bin/python')]);

  if (isWindows) {
    candidates.push(['py', '-3.12'], ['py', '-3.11'], ['py', '-3.10'], ['python']);
  } else {
    candidates.push(['python3'], ['python']);
  }

  for (const candidate of candidates) {
    const [command, ...args] = candidate;
    if (command.includes(path.sep) && !pathExists(command)) {
      continue;
    }

    const version = pythonVersion(command, args);
    if (isSupportedPython(version)) {
      console.log(`Using Python ${version.text}: ${candidate.join(' ')}`);
      return candidate;
    }
  }

  throw new Error('Python 3.10+ ni najden. Namesti Python 3.12 in poskusi ponovno.');
}

function venvPythonPath() {
  return path.join(VENV_DIR, isWindows ? 'Scripts\\python.exe' : 'bin/python');
}

function removeDirectory(directory) {
  fs.rmSync(directory, { recursive: true, force: true });
}

function ensureVenv(pythonCommand) {
  const currentVenvPython = venvPythonPath();
  if (pathExists(currentVenvPython)) {
    const version = pythonVersion(currentVenvPython);
    if (!isSupportedPython(version)) {
      console.log(`Removing outdated backend bundle virtual environment at ${VENV_DIR} ...`);
      removeDirectory(VENV_DIR);
    }
  }

  if (!pathExists(VENV_DIR)) {
    console.log(`Creating backend bundle virtual environment at ${VENV_DIR} ...`);
    const [command, ...args] = pythonCommand;
    run(command, [...args, '-m', 'venv', VENV_DIR]);
  }

  const python = venvPythonPath();
  if (!pathExists(python)) {
    throw new Error(`Bundle virtual environment Python ni najden v ${VENV_DIR}.`);
  }
  return python;
}

function main() {
  const pythonCommand = findPython();
  const venvPython = ensureVenv(pythonCommand);
  const distPath = path.join(BACKEND_DIR, 'dist');
  const workPath = path.join(BACKEND_DIR, 'build');

  run(venvPython, ['-m', 'pip', 'install', '--upgrade', 'pip', 'setuptools', 'wheel']);
  run(venvPython, ['-m', 'pip', 'install', '-r', path.join(BACKEND_DIR, 'requirements.txt'), 'pyinstaller']);

  run(venvPython, [
    '-m',
    'PyInstaller',
    '--clean',
    '--noconfirm',
    '--onefile',
    '--name',
    'atcconfmaker-engine',
    '--paths',
    BACKEND_DIR,
    '--collect-all',
    'ortools',
    '--collect-submodules',
    'uvicorn',
    '--collect-submodules',
    'fastapi',
    '--collect-submodules',
    'pydantic',
    '--distpath',
    distPath,
    '--workpath',
    workPath,
    path.join(ROOT_DIR, 'electron', 'backend_entry.py'),
  ]);

  console.log(`Backend sidecar built in ${distPath}`);
}

try {
  main();
} catch (error) {
  console.error(error instanceof Error ? error.message : error);
  process.exit(1);
}
