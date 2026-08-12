# ATCConfMaker

ATCConfMaker is a web-based planning prototype for calculating maximum sector hours for an ATC daily configuration.

For the controlled Windows deployment, build process, hashes and cyber-security review, see [docs/ATCConfMaker-security.md](docs/ATCConfMaker-security.md).

## Stack

- Frontend: React + TypeScript + Vite
- Backend: Python + FastAPI

## Requirements

Install these once on your machine:

- **Python 3.11+** (macOS: `python3 --version`)
- **Node.js LTS + npm** (macOS: `node --version` and `npm --version`)

The project creates and reuses a backend virtual environment at `backend/.venv`. That folder is ignored by git.

## One-command start: local Mac or GitHub Codespaces

From the repository root:

```bash
npm start
```

This runs `./scripts/start-dev.sh`, which:

1. finds `python3` / `python`,
2. creates `backend/.venv` if it does not exist,
3. installs missing backend dependencies from `backend/requirements.txt`,
4. installs frontend dependencies if `frontend/node_modules` is missing,
5. starts FastAPI on port `8000`,
6. starts Vite on port `5173`.

Open the app at:

- **Local Mac:** <http://localhost:5173>
- **Codespaces:** forwarded URL for port `5173`

The frontend calls relative `/api` URLs. Vite proxies those calls to `http://127.0.0.1:8000`, so both local and Codespaces development use the same browser-safe path.

### Useful environment overrides

If ports are busy, you can override them:

```bash
BACKEND_PORT=8010 FRONTEND_PORT=5174 npm start
```

If you want to choose a specific Python executable:

```bash
PYTHON=/opt/homebrew/bin/python3 npm start
```

If you want the virtual environment somewhere else:

```bash
BACKEND_VENV_DIR=/path/to/venv npm start
```

## Manual backend start

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Manual frontend start

```bash
cd frontend
npm install
npm run dev
```

Open <http://localhost:5173>. During development Vite proxies `/api` requests to `http://127.0.0.1:8000`.

## GitHub Codespaces notes

The recommended Codespaces setup is to open only the forwarded URL for port `5173`; the Vite dev server forwards `/api` requests to FastAPI internally. You can still override the API base URL manually if needed:

```bash
cd frontend
VITE_API_BASE_URL=https://your-codespace-name-8000.app.github.dev npm run dev
```

The backend also allows browser requests from GitHub's forwarded `*.app.github.dev` URLs during development, but the Vite proxy path is preferred because it is same-origin from the browser's point of view.

## Current MVP

The implemented program is **Kalkulator sektorskih ur**. It supports:

- calculating coverage from entered staff counts,
- calculating a generated staff plan from requested hourly sector openness,
- APS/ACS/FL licence split,
- paired lower/upper sector assignments,
- editable shift and rest rules.


##PRAVILA

Da, spodaj je trenutni seznam, kot ga razumem iz kode.

**Trde omejitve**
To so pravila, ki jih kalibracija ne sme kršiti.

- Izmena velja samo v svojih urah.
  Primer: `A7` samo v svojem časovnem oknu, `A21/V3` v nočnem oknu.

- Upoštevajo se samo aktivne izmene iz `Nastavitev pravil`.
  Če je `A12` izklopljena, je model ne sme uporabljati.

- Vsak odprt sektor potrebuje 2 človeka.

- Licenčna pravila sektorjev:
  `ALL` zahteva `2× FL`.
  `LOWER` sprejme `FL` ali `APS`.
  `UPPER`, `MID`, `HIGH`, `TOP` sprejmejo `FL` ali `ACS`.

- Ena oseba ne more delati na dveh mestih v isti uri.

- Pravilo ritma dela:
  privzeto največ `2` uri zapored na sektorju in potem vsaj `1` ura pavze.
  To je pravilo `2-1-2-1-2`.

- Maksimalne sektorske ure na osebo izhajajo iz izmene in zgornjega pravila.

- `Vi1/V1` in `Vi2/V2` ne smeta delati prvo in zadnjo uro svoje izmene.

- `Vi3/V3` ne sme delati prvo uro svoje izmene.

- Vloge imajo limite sektorskih ur:
  `V1` privzeto max `1`,
  `V2` privzeto max `1`,
  `V3` privzeto max `4`,
  `FMP` privzeto max `6`.

- Vodje izmen so vedno `FL`.
  Uporabnik jim v UI ne more spremeniti licence v APS/ACS.

- Če je vključeno obvezno vodstvo izmen:
  doda oziroma zahteva `V1/A7`, `V2/A14`, `V3/A21`, vsi `FL`.

- Nočna FL zahteva:
  če je vključena, zahteva nastavljeno število dodatnih `A21/FL` poleg `V3`.
  Privzeto so zahtevani `V3 + 3× A21/FL`, torej skupaj `4` nočni FL.

- Če je `FMP` vključen:
  doda se `FMP/A9/FL` kot posebna vloga.

- Fiksno vpisani ljudje so obvezni oziroma omejujejo generator.
  Če uporabnik ročno določi neko izmeno, jo mora model upoštevati oziroma ne sme preseči takšnih fiksnih omejitev.

- What-if zaklenjeni ljudje so obvezni.
  Ko uporabnik fiksira osebo/izmeno, mora model to upoštevati.

- Konkretni office po izmenah je obvezen, če je vpisan.
  Npr. `1× FL A7o` se mora vključiti kot office kandidat/oseba.

- Operativni office pool je omejen na vpisano število.
  Če je vpisan `FL office 1`, model ne sme uporabiti več kot enega.

- Limit ljudi je trd, kadar je vključen.
  Model ne sme preseči vpisanega števila rednih ljudi; office pool se obravnava posebej kot fallback.

- Pri vpisanih konkretnih licencah v načinu “iz ljudi” so FL/APS/ACS zgornje meje.
  Model ne sme izbrati več posamezne licence, kot jih je na voljo.

- Želena odprtost po urah je zgornji cilj.
  Model ne odpira več sektorjev, kot jih uporabnik nastavi za uro.

- Največ sektorjev hkrati je omejeno z nastavitvijo `max_sectors_per_hour`.

**Mehke omejitve / preference**
To so stvari, ki jih lahko kalibrirava brez kršenja pravil.

- Glavni cilj je najprej pokriti čim več sektorskih ur.
  To ima daleč največjo težo.

- Nato model kaznuje uporabo office oseb.
  Office naj bo zadnja možnost.

- Office delo ima dodatno kazen po uri:
  office je bolj zaželen na začetku ali koncu office izmene, manj v sredini.

- V načinu `Odprtost sektorjev` model kaznuje večje število izbranih ljudi.
  Če limit ni vklopljen, pri enaki pokritosti raje uporabi manj ljudi; v načinu `Število ljudi` pa ohrani točno vpisano število.

- Model kaznuje več neizkoriščene kapacitete.
  Pri enakem številu ljudi raje uporabi bolj “polno” sestavo.

- FMP ima slabšo prioriteto pri dodeljevanju na sektor.
  Dovoljen je, ampak naj se ne uporablja prehitro.

- V1/V2/V3 imajo slabšo prioriteto kot navadni kontrolorji.
  Ker so vodje, jih model ne porablja po nepotrebnem.

- Na LOWER model preferira APS pred FL.
  FL je dovoljen, ampak manj zaželen.

- Na UPPER/MID/HIGH/TOP model preferira ACS pred FL.
  FL je dovoljen, ampak manj zaželen.

- Če je vključeno ciljno razmerje licenc, model kaznuje odstopanje od FL/APS/ACS procentov.

- Če je vključena opcija “pri enaki pokritosti uporabi čim manj FL”, model dodatno kaznuje preveč FL.

- Pri 2 odprtih sektorjih ima model preferenčne sektorje po uri.
  Včasih raje `LOWER + UPPER`, v določenih urah raje `LOWER + TOP`.

- Izbira profila sektorjev ima mehko kazen.
  Model lahko izbere drugo dovoljeno kombinacijo, če to izboljša pokritost.

- Warm-start iz ročne baze je mehka smer.
  Ročna konfiguracija mu pomaga začeti blizu dobre rešitve, ampak CP-SAT jo lahko spremeni.

- Lokalno glajenje po rešitvi:
  če ne zmanjša pokritosti, poskuša ohraniti isti sektor oziroma manj menjavati levo/desno.

- Tie-breakerji pri sestavljanju kandidatov:
  model preferira izmene, ki bolje pokrivajo iskano odprtost, in ne želi preveč napolniti ene izmene po nepotrebnem.

**Za kalibracijo primerno**
Najbolj primerni kandidati za učenje iz fokus konfiguracij so:

- uteži za office,
- uteži za FMP,
- uteži za porabo V1/V2/V3,
- preferenca APS na LOWER,
- preferenca ACS na ostalih sektorjih,
- kazen za dodatnega človeka,
- kazen za neizkoriščeno kapaciteto,
- kazen za odstopanje od licenčnega razmerja,
- preference sektorjev pri 2 odprtih sektorjih,
- način izbire/warm-starta iz ročne baze,
- vrstni red faz: ročna baza, redna faza, polish, office fallback.

To zadnje je tisto, kar bi fokus audit lahko “učil”, medtem ko trda pravila ostanejo nedotaknjena.
