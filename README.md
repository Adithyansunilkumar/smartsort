# SmartSort

**SmartSort** is a local-first AI-assisted file and folder organization agent designed to help users intelligently structure, clean, and manage their files without compromising privacy or security.

---

## Architecture Overview

SmartSort uses a decoupled client-server architecture running entirely on the user's local machine:

```
┌─────────────────────────────────────────────────────────────┐
│                      User Workspace                         │
│                                                             │
│  ┌────────────────────────┐    REST / JSON    ┌──────────┐  │
│  │     React + Vite       │ ◄───────────────► │ FastAPI  │  │
│  │      Frontend          │  /api/health      │ Backend  │  │
│  │   (TypeScript UI)      │  /api/folder      │ (Secured)│  │
│  └────────────────────────┘                   └────┬─────┘  │
│                                                    │         │
│                                              Enforces Root   │
│                                              Security Boundary
│                                                    │         │
│                                                    ▼         │
│                                         ┌────────────────────┐
│                                         │  Authorized Root   │
│                                         │  Local Directory   │
│                                         └────────────────────┘
└─────────────────────────────────────────────────────────────┘
```

- **Frontend (`frontend/`)**: React 19, Vite, and TypeScript with vanilla CSS. Runs inside the browser sandbox with strictly zero direct filesystem access.
- **Backend (`backend/`)**: Python 3.12+ and FastAPI with Uvicorn. The sole authority for validating paths, canonicalizing directories, and enforcing the security boundary.
- **Phase 1 Boundary Enforcement**:
  - `POST /api/folder/select`: Canonicalizes and validates that a candidate directory exists, is a folder, and is readable before establishing it as the authorized root.
  - `GET /api/folder`: Returns the currently active authorized root.
  - `DELETE /api/folder`: Clears the authorized root.
  - `validate_path_in_root()`: Reusable security service strictly rejecting path traversal (`..`), sibling prefix collisions (e.g. `C:\data-other` vs `C:\data`), and external symlink escapes.
  - Recursive scanning and filesystem mutations remain decoupled for subsequent phases.

---

## Prerequisites

- **Node.js**: v18.0.0 or higher (v24+ tested)
- **npm**: v9.0.0 or higher
- **Python**: v3.10 or higher (v3.12 tested)

---

## Project Setup

### 1. Clone & Configuration
Copy the environment template files if customization is needed:
```bash
cp .env.example .env
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env
```

### 2. Backend Setup
1. Create a Python virtual environment:
   ```bash
   # Windows (PowerShell)
   python -m venv backend/.venv
   .\backend\.venv\Scripts\Activate.ps1

   # macOS / Linux
   python3 -m venv backend/.venv
   source backend/.venv/bin/activate
   ```

2. Install backend dependencies:
   ```bash
   pip install -r backend/requirements.txt
   ```

3. Run backend automated tests:
   ```bash
   pytest backend/tests -v
   ```

### 3. Frontend Setup
1. Install Node dependencies:
   ```bash
   cd frontend
   npm install
   ```

---

## Running the Application Locally

Run the backend and frontend development servers in separate terminal sessions:

### Terminal 1: Start Backend (FastAPI)
```bash
# Windows
.\backend\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000 --app-dir backend

# Or if virtualenv is activated from the backend directory:
cd backend
uvicorn app.main:app --reload --port 8000
```
The FastAPI backend starts at `http://127.0.0.1:8000`.

### Terminal 2: Start Frontend (Vite)
```bash
cd frontend
npm run dev
```
The Vite development server starts at `http://localhost:5173`.

---

## Verifying Frontend / Backend Communication

1. **System Health Check**:
   ```bash
   curl http://127.0.0.1:8000/api/health
   ```
   Expected response:
   ```json
   {
     "status": "ok",
     "app": "SmartSort",
     "version": "0.1.0"
   }
   ```

2. **Folder Authorization Endpoints**:
   - Check current root:
     ```bash
     curl http://127.0.0.1:8000/api/folder
     ```
   - Authorize a directory:
     ```bash
     curl -X POST http://127.0.0.1:8000/api/folder/select -H "Content-Type: application/json" -d "{\"path\": \"C:\\Users\\Username\\Documents\"}"
     ```
   - Clear authorized root:
     ```bash
     curl -X DELETE http://127.0.0.1:8000/api/folder
     ```

3. **Frontend UI**:
   - Open `http://localhost:5173` in a web browser.
   - Observe both the **Backend API** status card (`● Connected`) and the **Authorized Root Directory** management card.
   - Enter a local folder path and click **Authorize Folder**.
   - Observe that the backend validates and sets the canonical path with a green `● Authorized Root Active` badge.
   - Test entering an invalid or non-existent path to verify that structured, human-readable errors are surfaced without exposing stack traces.

---

## Testing

### Automated Backend Tests (16 Tests)
```bash
.\backend\.venv\Scripts\python.exe -m pytest backend/tests -v
```
Covers:
- Valid root establishment
- Non-existent path rejection
- File instead of directory rejection
- Empty path rejection
- Reset/clear root endpoint
- Nested path containment verification
- Root path acceptance
- Sibling directory rejection
- Parent traversal (`..`) escape prevention
- Prefix collision (`<root>-other`) prevention
- Unconfigured state handling
- Health check and CORS headers

### Frontend TypeScript & Build Verification
```bash
npm --prefix frontend run build
```

---

## Security & Architectural Constraints

- **Strict Local-First Boundary**: The frontend cannot read or write user filesystem files directly.
- **Backend as Security Authority**: All candidate paths must be canonicalized and checked against the active authorized root via `validate_path_in_root()`.
- **No Path Traversal**: Traversal sequences (`..`) and prefix collisions are detected and blocked.
- **Controlled CORS Policy**: Configured specifically for local development origins (`http://localhost:5173`, `http://127.0.0.1:5173`).
- **No Committed Secrets**: `.gitignore` prevents committing `.env`, virtual environments (`.venv`), and build artifacts.
