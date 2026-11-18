# MercatoIQ

Plateforme d'analyse sportive et financière (« Moneyball ») croisant les performances sur le terrain (buts, passes décisives, minutes jouées, cartons, remplacements) avec les réalités du marché des transferts (indemnités de transfert et valorisations marchandes) sur l'**UEFA Champions League** et le **Top 5 européen** depuis 2012.

---

## Structure du Monorepo

- **`/backend`** *(submodule)* : API REST **NestJS (TypeScript)** (`/api/v1/...`), authentification JWT + RBAC (`user`, `admin`), Swagger (`/api/docs`) et déclenchement du pipeline via `child_process.spawn`.
- **`/pipeline`** : Pipeline **Python 3** (`pandas`, `numpy`, `pymongo`, `scikit-learn`) — Architecture Médaillon (**Bronze -> Silver -> Gold**) avec failover local dans `/pipeline/backup_data/`.
- **`/frontend`** *(submodule)* : Client Web **React (Vite)** hébergeant le **User Dashboard (`/dashboard`)** et l'**Admin Dashboard (`/admin`)**.
- **`/scripts/mongo-init.js`** : Script d'initialisation automatique des collections et des index uniques/composés MongoDB.

---

## Démarrage Rapide (Setup Local)

### 1. Cloner avec les submodules et configurer `.env`
```bash
git submodule update --init --recursive
cp .env.example .env
```

### 2. Lancer MongoDB via Docker Compose
```bash
docker compose up -d
```
Le conteneur `mercatoiq-mongodb` écoute sur `localhost:27017` et exécute automatiquement `scripts/mongo-init.js` au premier démarrage.

### 3. Installer l'environnement Python du Pipeline
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r pipeline/requirements.txt
python3 pipeline/run_pipeline.py --trigger manual --layer all
```

---

## Git Flow de l'équipe

- **`main`** : Branche stable (livrables de fin de sprint / production).
- **`develop`** : Branche d'intégration continue de l'équipe.
- **`feature/<ticket-id>-<nom>`** : Branches de travail par ticket (ex: `feature/infra-01-setup`, `feature/pipe-01-bronze`).
