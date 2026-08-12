# ATCConfMaker – tehnični in varnostni opis

Ta dokument je namenjen službi za kibernetsko varnost, skrbnikom delovnih postaj in upravljavcem application allow-listinga (na primer Cortex XDR). Opisuje različico ATCConfMaker 0.1.0 in paket `ATCConfMakerSetup.exe`.

## 1. Namen programa

ATCConfMaker je lokalna namizna aplikacija za pripravo, optimizacijo, primerjavo in izvoz konfiguracij sektorskih ur. Optimizacijski del uporablja Google OR-Tools CP-SAT. Program ne upravlja letalskega prometa in ni operativna ATC nadzorna komponenta; je orodje za načrtovanje.

Program obdeluje vhodne podatke lokalno. V aplikacijski kodi ni telemetrije, oglaševalskih knjižnic, oddaljene prijave, sinhronizacije v oblak ali samodejnega pošiljanja konfiguracij.

## 2. Priporočeni distribucijski paket

Za nadzorovano službeno okolje je namenjen:

```text
ATCConfMakerSetup.exe
```

To je uporabniški NSIS namestitveni program. Ne zahteva administratorskih pravic in program privzeto namesti v stalno mapo uporabnikovega profila, praviloma:

```text
%LOCALAPPDATA%\Programs\ATCConfMaker\
```

Točno pot je treba potrditi na pilotni delovni postaji, saj jo lahko spremeni organizacijska politika Windows profila.

Namestitveni paket se od portable paketa razlikuje v pomembni varnostni lastnosti: Pythonov engine je izdelan v načinu PyInstaller `onedir`. Izvršilne datoteke in DLL-i so zato že nameščeni na stalni poti in se pri vsakem zagonu ne ustvarjajo v naključni `%TEMP%\_MEI...` mapi.

Portable paket `ATCConfMaker-windows.exe` za nadzorovano okolje ni priporočen, ker pred zagonom razpakira Electron v `%TEMP%` in od tam zažene procese.

### Zahteve za delovno postajo

- Windows 10 ali Windows 11, 64-bitni (x64),
- običajen uporabniški račun; administratorske pravice niso potrebne,
- pravica pisanja v `%LOCALAPPDATA%`, `%APPDATA%` in med namestitvijo v `%TEMP%`,
- priporočeno najmanj 1 GB prostega prostora za namestitev, podatke in začasni namestitveni payload,
- dovoljena lokalna loopback povezava `127.0.0.1:8765`,
- dovoljeno izvajanje datotek, navedenih v SHA-256 manifestu.

Na računalniku ni treba posebej namestiti Pythona, Node.js, brskalnika, Microsoft Excel ali Microsoft Visual C++ Redistributable. Potrebni runtimei so vključeni v namestitveni paket. Internetna povezava je potrebna za izdelavo paketa v GitHub Actions, ne pa za namestitev ali normalno uporabo že izdelanega paketa.

## 3. Arhitektura

Program ima dve lokalni komponenti:

```text
ATCConfMaker.exe
├── Electron/Chromium uporabniški vmesnik
├── dodatni Electron procesi z istim imenom
│   ├── renderer
│   ├── GPU
│   └── utility procesi
└── resources\backend\atcconfmaker-engine\atcconfmaker-engine.exe
    ├── Python 3.12 runtime
    ├── FastAPI/Uvicorn
    └── Google OR-Tools CP-SAT
```

Electron zaradi Chromiumovega večprocesnega modela normalno zažene več procesov `ATCConfMaker.exe` z različnimi argumenti `--type=...`. To ni več kopij programa, temveč ločeni procesi za uporabniški vmesnik, grafiko in pomožne storitve Chromiuma.

`ATCConfMaker.exe` zažene en lokalni backend `atcconfmaker-engine.exe`. Nameščena različica enginea ne potrebuje sistemsko nameščenega Pythona.

## 4. Izvršilne datoteke in allow-listing

Glavni pričakovani izvršilni datoteki po namestitvi sta:

```text
ATCConfMaker.exe
resources\backend\atcconfmaker-engine\atcconfmaker-engine.exe
```

V podmapi enginea so še Pythonove in OR-Toolsove `.dll` oziroma `.pyd` knjižnice. Celoten seznam datotek namestitvenega payloada je ob vsakem buildu zapisan v:

```text
ATCConfMakerSetup-SHA256.txt
```

Manifest vsebuje SHA-256 namestitvenega EXE in vsake datoteke aplikacijskega payloada, vključno z `app.asar`, `.exe`, `.dll`, `.pyd`, `.node` in podatkovnimi datotekami. Manifest je treba obravnavati kot avtoritativni seznam za konkretni build. Vsak nov build spremeni vsaj del hashov in zato zahteva novo odobritev, če se uporablja izključno hash-based allow-listing. NSIS lahko ob namestitvi ustvari še odstranjevalnik; ta ni del aplikacijskega runtime payloada in ga mora IT obravnavati skladno s svojo politiko za installerje.

Trenutni paketi niso digitalno podpisani z Authenticode certifikatom. Windows oziroma Cortex jih zato ne moreta zanesljivo dovoljevati po preverjenem založniku. Dolgoročno priporočilo je podpis vseh release artefaktov z organizacijskim code-signing certifikatom in allow-listing po založniku/certifikatu.

Ker je per-user namestitvena mapa zapisljiva prijavljenemu uporabniku, dovoljenje samo na podlagi poti ni dovolj varen nadzor. Uporabiti je treba hashe iz manifesta ali digitalni podpis; alternativa za prihodnji IT-upravljani deployment je per-machine namestitev v `%ProgramFiles%` z administratorsko zaščitenimi ACL-i.

## 5. Omrežna komunikacija

Backend posluša izključno na IPv4 loopback naslovu:

```text
127.0.0.1:8765/TCP
```

Protokol med uporabniškim vmesnikom in backendom je HTTP. TLS se ne uporablja, ker promet ne zapusti lokalnega računalnika. Program ob zagonu preverja:

```text
GET http://127.0.0.1:8765/api/health
```

Uporabniški vmesnik nato uporablja lokalne poti `/api/...` za izračune, konfiguracije in izvoze.

V pregledani aplikacijski kodi ni zahtev na internetne domene. Za normalno izvajanje ni potreben dostop do interneta, DNS, proxyja ali oddaljenega strežnika. Odpreti je treba samo lokalno loopback komunikacijo med procesoma istega uporabnika. Backend ni vezan na `0.0.0.0`, LAN naslov ali zunanji omrežni vmesnik.

Znana omejitev: lokalni API nima uporabniške prijave. Dosegljiv je samo z istega računalnika in samo med delovanjem aplikacije, vendar ga lahko teoretično pokliče drug lokalni proces v istem času. API nima povišanih pravic in ne omogoča splošnega izvajanja ukazov. Kot dodatni varnostni ukrep je mogoče v prihodnji različici dodati naključni sejni žeton.

Če je port 8765 že zaseden ali ga lokalna varnostna politika blokira, se backend ne zažene in aplikacija po največ 120 sekundah prikaže napako. Daljši zagonski prag dopušča, da EDR ob prvem zagonu pregleda nameščene knjižnice.

## 6. Datotečni dostop

### Datoteke, ki jih program bere iz namestitvene mape

```text
resources\data\Konfiguracije OKZP.xlsx
resources\data\konfiguracije_okzp_obogateno_vlimiti.csv
resources\data\user_configurations.json
resources\app.asar
resources\backend\atcconfmaker-engine\...
```

### Datoteke, ki jih program zapisuje

Electron uporablja standardno uporabniško podatkovno mapo, praviloma:

```text
%APPDATA%\ATCConfMaker\
```

Namenske aplikacijske datoteke so:

```text
%APPDATA%\ATCConfMaker\user_configurations.json
%APPDATA%\ATCConfMaker\pattern-cache\patterns.json
```

Electron/Chromium lahko v isti uporabniški mapi ustvari še standardne datoteke, kot so `Preferences`, `Local Storage`, `Cache`, `Code Cache` in `GPUCache`.

Ob prvem zagonu lahko program samo prebere stare podatke iz `%APPDATA%\KonfMaker\` in jih kopira v novo mapo `ATCConfMaker`. Stare mape ne briše.

Excel/CSV izvozi se ustvarijo samo na izrecno zahtevo uporabnika in se shranijo na lokacijo, ki jo določi brskalniški oziroma sistemski dialog za prenos. Program ne izvaja makrov iz Excelovih datotek.

Podatki na disku niso dodatno šifrirani na aplikacijski ravni. Za zaščito se uporabljajo Windows ACL-i uporabniškega profila in morebitno organizacijsko šifriranje diska (na primer BitLocker).

## 7. Pravice in sistemske spremembe

Namestitev je per-user in ne zahteva UAC/admin žetona. Program deluje z običajnimi pravicami prijavljenega uporabnika.

Programska koda ne namešča in ne ustvarja:

- Windows storitev,
- gonilnikov ali kernel komponent,
- načrtovanih opravil,
- brskalniških razširitev,
- PowerShell skript,
- oddaljenih poslušalnih vrat,
- trajnih administratorskih komponent.

NSIS installer ustvari običajne per-user podatke za odstranitev aplikacije ter bližnjice, ki jih standardno uporablja Electron Builder. Aplikacijska koda sama ne spreminja registra za poslovno logiko.

Nameščena različica pri normalnem zagonu ne uporablja `taskkill.exe`; fiksno nameščeni backend konča neposredno. Installer oziroma odstranjevalnik lahko uporablja standardne Windows/NSIS mehanizme za namestitev ali odstranitev.

## 8. Uporaba začasnih map

Namestitveni EXE med samo namestitvijo začasno razpakira namestitveni payload. To je enkratno pričakovano vedenje installerja.

Po namestitvi se `ATCConfMaker.exe`, `atcconfmaker-engine.exe`, Python runtime in OR-Tools knjižnice izvajajo iz stalne namestitvene poti. Pri vsakem zagonu se ne ustvarja nova izvršilna koda v `%TEMP%`.

Electron/Chromium in Windows lahko še vedno uporabljata `%TEMP%` za običajne neizvršilne začasne datoteke. Zahteva tega paketa je odstranitev sprotnega razpakiranja izvršilne kode, ne popolna prepoved vseh začasnih datotek.

## 9. Glavne tehnologije in različice

Build je zasnovan z naslednjimi glavnimi komponentami:

| Komponenta | Različica/vir | Namen |
|---|---:|---|
| Electron | 43.4.0 | Namizno okno in Chromium runtime |
| Node.js v CI | 22 | Build Electron aplikacije |
| React | 19.2.8 | Uporabniški vmesnik |
| Python | 3.12 | Lokalni optimizacijski backend |
| PyInstaller | 6.21.0 | Pakiranje Python runtimea v `onedir` |
| FastAPI | 0.141.1 | Lokalni HTTP API |
| Uvicorn | 0.52.1 | Lokalni HTTP strežnik |
| Pydantic | 2.13.4 | Validacija podatkov |
| Google OR-Tools | 9.15.6755 | CP-SAT optimizacijski solver |
| electron-builder | 26.15.3 | Windows/NSIS pakiranje |

Pythonove verzije so zapisane v `backend/requirements.txt` in `backend/requirements-desktop-build.txt`. Node/Electron verzije so določene v `package-lock.json`; frontend uporablja ločen `frontend/package-lock.json`.

Ob pripravi tega builda je `npm audit` za zaklenjene korenske Electron/build odvisnosti in ločeno za frontend odvisnosti vrnil 0 znanih ranljivosti. Tudi `pip-audit` za nameščene Python odvisnosti ni našel znanih ranljivosti. Rezultat je časovno omejen na trenutek preverjanja in ni nadomestilo za ponovno preverjanje ob vsakem prihodnjem releasu.

## 10. Build in sledljivost

Windows build izvaja GitHub Actions na uradnem okolju `windows-latest`:

1. checkout izvorne kode,
2. namestitev Node.js 22,
3. namestitev Python 3.12,
4. `npm ci`,
5. namestitev zaklenjenih Python odvisnosti,
6. PyInstaller `onedir` build enginea,
7. Vite/React build,
8. Electron Builder NSIS build,
9. `npm audit` in `pip-audit` varnostno preverjanje zaklenjenih odvisnosti,
10. izračun SHA-256 manifesta.

Release naj vsebuje skupaj:

```text
ATCConfMakerSetup.exe
ATCConfMakerSetup-SHA256.txt
ATCConfMaker-security.md
```

Za formalno dobavno verigo je priporočljivo še: zaščita GitHub veje, pregled sprememb, podpisani tagi, code signing, hramba build logov in neodvisno malware skeniranje artefakta.

## 11. Pričakovana poraba virov

Electron zaradi Chromiuma uporablja več procesov in več pomnilnika kot klasična enoprocesna aplikacija. OR-Tools lahko med optimizacijo več minut intenzivno uporablja CPU in več niti. Privzeti časovni limit solverja je do 600 sekund za zahtevnejše izračune. Visoka poraba CPU med aktivnim izračunom je pričakovana, ne pomeni pa omrežnega rudarjenja ali ozadnega opravila.

Po zaprtju glavnega okna se backend ustavi. Program nima namernega stalnega ozadnega procesa.

## 12. Povzetek znanih tveganj

| Tveganje | Trenutno stanje | Predlagan nadzor |
|---|---|---|
| Nepodpisani binarni artefakti | Prisotno | SHA-256 allow-listing; dolgoročno code signing |
| Per-user mapa je uporabniško zapisljiva | Prisotno | Ne dovoliti samo po poti; uporabiti hash ali podpis |
| Več Electron procesov | Pričakovano | Dovoliti podpis/hash `ATCConfMaker.exe` in njegove child procese |
| Lokalni child proces enginea | Pričakovano | Dovoliti fiksno pot in hash `atcconfmaker-engine.exe` |
| Lokalni API brez avtentikacije | Omejeno na 127.0.0.1 | Endpoint dovoliti samo lokalno; po potrebi dodati sejni žeton |
| Fiksni port 8765 | Možen konflikt | Rezervirati/dovoliti loopback port ali v prihodnje uporabiti dinamični port |
| Visoka CPU poraba | Med solverjem pričakovana | Informirati SOC; omejitev trajanja je nastavljiva |
| Podatki niso aplikacijsko šifrirani | Lokalni uporabniški profil | Windows ACL + BitLocker/organizacijska politika |

## 13. Predlagani postopek odobritve

1. IT pridobi vse tri release datoteke iz istega builda.
2. Preveri SHA-256 installerja proti manifestu.
3. Namesti paket na izolirano pilotno napravo brez administratorskih pravic.
4. Primerja hashe vseh nameščenih datotek z manifestom.
5. V Cortexu dovoli fiksno namestitveno pot in navedene hashe.
6. Dovoli procesno razmerje `ATCConfMaker.exe` → `atcconfmaker-engine.exe`.
7. Dovoli lokalni TCP promet na `127.0.0.1:8765`; oddaljeni promet lahko ostane blokiran.
8. Dovoli zapis v `%APPDATA%\ATCConfMaker\` in običajne Chromium cache mape znotraj nje.
9. Izvede test zagona, test izračuna, Excel izvoza in pravilnega zaprtja procesov.

## 14. Diagnostični ukazi za IT

SHA-256 installerja:

```powershell
Get-FileHash .\ATCConfMakerSetup.exe -Algorithm SHA256
```

Preverjanje podpisa trenutnega nepodpisanega builda:

```powershell
Get-AuthenticodeSignature .\ATCConfMakerSetup.exe
```

Pričakovani rezultat trenutnega builda je `NotSigned`.

Primerjava vseh nameščenih datotek z manifestom (ukaz zaženite v mapi, kjer je manifest):

```powershell
$appRoot = Join-Path $env:LOCALAPPDATA 'Programs\ATCConfMaker'
$failed = Get-Content .\ATCConfMakerSetup-SHA256.txt |
  Where-Object { $_ -match '^(?<hash>[0-9a-f]{64})  APP/(?<path>.+)$' } |
  ForEach-Object {
    $expected = $Matches.hash
    $relative = $Matches.path -replace '/', '\'
    $file = Join-Path $appRoot $relative
    if (-not (Test-Path $file)) {
      [pscustomobject]@{ Path = $relative; Status = 'MANJKA' }
    } elseif ((Get-FileHash $file -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) {
      [pscustomobject]@{ Path = $relative; Status = 'HASH SE NE UJEMA' }
    }
  }
$failed
```

Če ukaz ne izpiše ničesar, so vse datoteke, navedene v manifestu, prisotne z ustreznimi hashi.

Procesi:

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -in @('ATCConfMaker.exe', 'atcconfmaker-engine.exe') } |
  Select-Object ProcessId, ParentProcessId, Name, ExecutablePath, CommandLine
```

Lokalni port:

```powershell
Get-NetTCPConnection -LocalPort 8765 -ErrorAction SilentlyContinue |
  Select-Object LocalAddress, LocalPort, State, OwningProcess
```

Health check:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/api/health
```

Pričakovani odgovor med delovanjem aplikacije je:

```json
{"status":"ok"}
```

Če se po zagonu nič ne prikaže, naj IT najprej preveri Cortex dogodke za blokado `ATCConfMaker.exe`, `atcconfmaker-engine.exe` ali katere od knjižnic v `resources\backend\atcconfmaker-engine\_internal\`, nato zasedenost porta 8765 in pravice pisanja v `%APPDATA%\ATCConfMaker\`.
