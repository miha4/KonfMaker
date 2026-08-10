if (process.platform !== 'win32') {
  console.error(
    [
      'Windows paket mora biti zgrajen na Windows okolju.',
      'Razlog: PyInstaller mora ustvariti atcconfmaker-engine.exe, tega ne moremo pravilno narediti na macOS.',
      'Uporabi GitHub Actions workflow "Build Windows Desktop" ali poženi npm run desktop:build:win na Windows računalniku.',
    ].join('\n'),
  );
  process.exit(1);
}
