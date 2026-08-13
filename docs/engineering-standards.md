# Llm-budget-gateway Engineering Standards

> **Minden agent kötelezően olvassa el** a kódírás előtt (worker prompt hivatkozik rá).
> Ez a fájl a projekt "modern, felhasználóbarát, megbízható" definíciója —
> ami itt szerepel, az **ellenőrizhető követelmény**, nem ízlés.

## 1. Felhasználói felület / UX

- [ ] Minden felhasználói hibaüzenet magyar nyelven, érthető, cselekvésre vezető szöveggel jelenik meg
- [ ] Minden űrlap validációja valós időben (submit előtt) és a submit után is jelzi a hibát
- [ ] Minden gombnak/ikonnak van hozzáférhető neve (aria-label / title)
- [ ] A loading állapotok minden async műveletnél megjelennek (spinner/skeleton)
- [ ] A hibák soha nem "csendben" nyelődnek el — a felhasználó mindig kap visszajelzést
- [ ] Responsive: a fő felhasználói utak mobil nézetben is használhatók

## 2. API / Backend

- [ ] Minden API végpont hibája strukturált JSON: `{ "error": { "code": ..., "message": ... } }`
- [ ] Minden külső hívás (LLM, HTTP, DB) rendelkezik timeout-tal és hiba-kezeléssel
- [ ] Sose logolunk titkokat (API kulcs, jelszó, token)
- [ ] Minden változást igénylő végpont validálja a bemenetet (Pydantic/séma)
- [ ] Idempotencia: az ismételt kérések nem okoznak duplikált mellékhatást

## 3. Adat / Perzisztencia

- [ ] Minden séma-változás migrációval érkezik (nem kézi DDL)
- [ ] A kritikus írások tranzakcióban futnak (commit/rollback)
- [ ] A személyes adatok (PII) soha nem kerülnek logba / analitikába nyersen

## 4. Kódminőség

- [ ] TDD: minden új viselkedéshez előbb piros teszt, aztán implementáció
- [ ] Nincs dead code, kommentezett kód, debug print
- [ ] A függvények/modulok nevei a szándékot írják le, nem az implementációt
- [ ] DRY: az ismétlődő logika kiemelve, nem copy-paste
- [ ] A típusok explicit (Python: type hints; TS: strict)

## 5. Tesztelés

- [ ] A teljes suite zöld (0 failed) — a pre-existing hibák deselectelve a known-fail listából
- [ ] Minden javított hiba kap regressziós tesztet (lásd docs/decisions/)
- [ ] A kritikus felhasználói utak lefedettek (happy path + edge + error)

## 6. Biztonság

- [ ] Nincs hardcodeolt titok a repóban (.env a gitignore-ban)
- [ ] A bemenetek soha nem kerülnek közvetlenül SQL-be / shell-be (paraméterezés)
- [ ] A hozzáférések minimáljogosultság elvén működnek
- [ ] A függőségek verziója pinelve (pyproject.toml / package.json)

---

## Kapcsolódó fájlok

- `docs/decisions/` — javított hibák és döntések (anti-minták + helyes minták)
- `docs/specs/` — feature specifikációk (kanonikus követelmény)
- `shared/templates/task-contract.md` — task body kontraktus
