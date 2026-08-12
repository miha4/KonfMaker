# ATCConfMaker Desktop

ATCConfMaker desktop uporablja isti React frontend in isti FastAPI backend kot razvojna spletna verzija. Electron samo zapakira uporabniški vmesnik in lokalno zažene Python engine.

## Razvojni zagon

```bash
npm install
npm run desktop:dev
```

`desktop:dev` naredi tri stvari:

1. preveri `backend/.venv` in backend odvisnosti,
2. zgradi frontend z `VITE_API_BASE_URL=http://127.0.0.1:8765`,
3. odpre Electron okno in zažene FastAPI backend na `127.0.0.1:8765`.

## Mac paket

```bash
npm run desktop:build:mac
```

Ta ukaz najprej zgradi Python sidecar `backend/dist/atcconfmaker-engine`, nato React frontend in nato Electron `.dmg`/`.zip` v mapi `release/`.

## Windows paket

```bash
npm run desktop:build:win:setup
```

Ukaz ustvari samo `ATCConfMakerSetup.exe`. Namestitveni program brez administratorskih pravic enkrat namesti aplikacijo v stalno uporabniško mapo. Backend je izdelan v načinu PyInstaller `onedir`, zato se izvršilna koda ob vsakem zagonu ne razpakira v naključno `%TEMP%\\_MEI...` mapo.

Windows build poženi na Windows računalniku ali Windows CI okolju, ker mora PyInstaller zgraditi Windows `atcconfmaker-engine.exe`. Build iz macOS okolja ni dovolj, ker bi dobil macOS Python sidecar.
Zaradi tega `npm run desktop:build:win:setup` na macOS/Linux namenoma prekine z razlago.

Pripravljen je tudi ročni GitHub Actions workflow:

1. Potisni spremembe na GitHub.
2. Odpri `Actions`.
3. Izberi `Build Windows Desktop`.
4. Klikni `Run workflow`.
5. Po koncu prenesi artifact `atcconfmaker-windows`.

Workflow ustvari Windows namestitveni paket in spremljevalni datoteki:

- `download-site/downloads/ATCConfMakerSetup.exe` (stalna namestitev za Windows)
- `download-site/downloads/ATCConfMakerSetup-SHA256.txt` (hashi vseh datotek namestitvenega payloada)
- `download-site/downloads/ATCConfMaker-security.md` (tehnični opis za kibernetsko varnost)

## Arnes download stran

Mapa `download-site/` je statična stran za prenos. Na Arnes FTP jo lahko naložiš kot npr. `/konfmaker/`, pakete pa daš v `/konfmaker/downloads/`.

Predvideni imeni paketov:

- `downloads/ATCConfMaker-mac.dmg`
- `downloads/ATCConfMakerSetup.exe`

## Podatki v desktop aplikaciji

Zapakirana aplikacija bere ročni Excel iz resources:

- `data/Konfiguracije OKZP.xlsx`

Za seznam in metapodatke ročnih konfiguracij trenutno uporablja tudi obogateno knjižnico:

- `data/konfiguracije_okzp_obogateno_vlimiti.csv`

Uporabniško shranjene konfiguracije in pattern cache se pišejo v uporabnikov lokalni app data direktorij, ne nazaj v aplikacijski paket.
