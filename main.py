from fastapi import FastAPI, HTTPException, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from datetime import datetime, timedelta, timezone
import json
import os
import secrets

app = FastAPI(title="World Cup 2026 API", description="Premium predictions only")

# Admin auth
security = HTTPBasic()
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "cup2026")
DATA_FILE = "data.json"
templates = Jinja2Templates(directory="templates")

def verify_admin(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username.encode("utf8"), ADMIN_USERNAME.encode("utf8"))
    correct_password = secrets.compare_digest(credentials.password.encode("utf8"), ADMIN_PASSWORD.encode("utf8"))
    if not (correct_username and correct_password):
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True

def load_data():
    if not os.path.exists(DATA_FILE):
        default = {"tournament": "FIFA World Cup 2026", "last_updated": datetime.now().isoformat(), "groups": [], "teams": {}, "matches": [], "knockout_matches": []}
        save_data(default)
        return default
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_data(data):
    data["last_updated"] = datetime.now().isoformat()
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def get_next_id(data):
    all_ids = [m["id"] for m in data.get("matches", [])] + [m["id"] for m in data.get("knockout_matches", [])]
    return max(all_ids, default=0) + 1

def is_prediction_available(match_datetime_str: str) -> bool:
    try:
        match_dt = datetime.strptime(match_datetime_str, "%Y-%m-%d %H:%M")
        match_dt = match_dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return now >= match_dt - timedelta(hours=24)
    except:
        return True

def default_prediction():
    return {
        "winner": "TBD", "home_goals": 0, "away_goals": 0, "total_goals": 0,
        "both_teams_score": False, "over_2_5": False,
        "corners": {"home": 0, "away": 0, "total": 0},
        "fouls": {"home": 0, "away": 0, "total": 0},
        "yellow_cards": {"home": 0, "away": 0, "total": 0},
        "red_cards": {"home": 0, "away": 0, "total": 0}
    }

def is_manually_edited(pred: dict) -> bool:
    """Return True if any prediction value is non‑default (i.e., manually edited)."""
    return (
        pred.get("winner") != "TBD" or
        pred.get("home_goals", 0) != 0 or
        pred.get("away_goals", 0) != 0 or
        pred.get("corners", {}).get("total", 0) != 0 or
        pred.get("fouls", {}).get("total", 0) != 0 or
        pred.get("yellow_cards", {}).get("total", 0) != 0 or
        pred.get("red_cards", {}).get("total", 0) != 0
    )

# ---------- PREMIUM endpoint with {equipe1}/{equipe2} (bidirectional) ----------
@app.get("/premium/predict/{equipe1}/{equipe2}", tags=["Premium"])
def premium_prediction(equipe1: str, equipe2: str):
    data = load_data()
    all_matches = data["matches"] + data.get("knockout_matches", [])

    def format_response(m):
        pred = m["prediction"]
        if is_manually_edited(pred):
            return {
                "match_id": m["id"],
                "home_team": m["home_team"],
                "away_team": m["away_team"],
                "date": m["date"],
                "stage": m.get("stage"),
                "prediction": pred
            }
        match_dt_str = m.get("datetime")
        if not match_dt_str:
            match_dt_str = m.get("date", "2026-06-11") + " 12:00"
        if not is_prediction_available(match_dt_str):
            return {
                "match": f"{m['home_team']} vs {m['away_team']}",
                "date": m.get("date"),
                "message": "Full predictions will be available 24 hours before the match."
            }
        return {
            "match_id": m["id"],
            "home_team": m["home_team"],
            "away_team": m["away_team"],
            "date": m["date"],
            "stage": m.get("stage"),
            "prediction": pred
        }

    # Try normal order (equipe1 = home, equipe2 = away)
    for m in all_matches:
        if m["home_team"].lower() == equipe1.lower() and m["away_team"].lower() == equipe2.lower():
            return format_response(m)

    # Try reversed order (equipe1 = away, equipe2 = home)
    for m in all_matches:
        if m["home_team"].lower() == equipe2.lower() and m["away_team"].lower() == equipe1.lower():
            return format_response(m)

    raise HTTPException(404, f"Match not found between {equipe1} and {equipe2}")

# ---------- Statistics (premium) ----------
@app.get("/premium/stats", tags=["Premium"])
def premium_stats():
    data = load_data()
    all_matches = data["matches"] + data.get("knockout_matches", [])
    total_goals = sum(m["prediction"]["total_goals"] for m in all_matches)
    total_corners = sum(m["prediction"]["corners"]["total"] for m in all_matches)
    total_yellow = sum(m["prediction"]["yellow_cards"]["total"] for m in all_matches)
    return {
        "total_matches": len(all_matches),
        "total_goals": total_goals,
        "avg_goals": round(total_goals/len(all_matches),2) if all_matches else 0,
        "total_corners": total_corners,
        "total_yellow_cards": total_yellow,
        "btts_count": sum(1 for m in all_matches if m["prediction"]["both_teams_score"])
    }

# ---------- Admin dashboard (HTML) ----------
@app.get("/admin", response_class=HTMLResponse, dependencies=[Depends(verify_admin)])
def admin_panel(request: Request):
    data = load_data()
    return templates.TemplateResponse("admin.html", {"request": request, "matches": data.get("matches", []), "knockout": data.get("knockout_matches", [])})

@app.post("/admin/add_match", dependencies=[Depends(verify_admin)])
async def add_match(
    home_team: str = Form(...), away_team: str = Form(...), date: str = Form(...), time: str = Form(...),
    stage: str = Form(...), group: str = Form(""), winner: str = Form(...),
    home_goals: int = Form(...), away_goals: int = Form(...),
    corners_home: int = Form(0), corners_away: int = Form(0),
    fouls_home: int = Form(0), fouls_away: int = Form(0),
    yellow_home: int = Form(0), yellow_away: int = Form(0),
    red_home: int = Form(0), red_away: int = Form(0),
    both_teams_score: bool = Form(False), over_2_5: bool = Form(False)
):
    data = load_data()
    pred = {
        "winner": winner, "home_goals": home_goals, "away_goals": away_goals, "total_goals": home_goals+away_goals,
        "both_teams_score": both_teams_score, "over_2_5": over_2_5,
        "corners": {"home": corners_home, "away": corners_away, "total": corners_home+corners_away},
        "fouls": {"home": fouls_home, "away": fouls_away, "total": fouls_home+fouls_away},
        "yellow_cards": {"home": yellow_home, "away": yellow_away, "total": yellow_home+yellow_away},
        "red_cards": {"home": red_home, "away": red_away, "total": red_home+red_away}
    }
    new_match = {
        "id": get_next_id(data), "stage": stage, "group": group.upper() if stage=="group" else None,
        "home_team": home_team, "away_team": away_team, "date": date, "datetime": f"{date} {time}",
        "prediction": pred
    }
    if stage == "group":
        data["matches"].append(new_match)
    else:
        data.setdefault("knockout_matches", []).append(new_match)
    save_data(data)
    return RedirectResponse(url="/admin", status_code=303)

@app.post("/admin/update_match/{match_id}", dependencies=[Depends(verify_admin)])
async def update_match(match_id: int, winner: str = Form(...), home_goals: int = Form(...), away_goals: int = Form(...),
    corners_home: int = Form(0), corners_away: int = Form(0), fouls_home: int = Form(0), fouls_away: int = Form(0),
    yellow_home: int = Form(0), yellow_away: int = Form(0), red_home: int = Form(0), red_away: int = Form(0),
    both_teams_score: bool = Form(False), over_2_5: bool = Form(False)):
    data = load_data()
    new_pred = {
        "winner": winner, "home_goals": home_goals, "away_goals": away_goals, "total_goals": home_goals+away_goals,
        "both_teams_score": both_teams_score, "over_2_5": over_2_5,
        "corners": {"home": corners_home, "away": corners_away, "total": corners_home+corners_away},
        "fouls": {"home": fouls_home, "away": fouls_away, "total": fouls_home+fouls_away},
        "yellow_cards": {"home": yellow_home, "away": yellow_away, "total": yellow_home+yellow_away},
        "red_cards": {"home": red_home, "away": red_away, "total": red_home+red_away}
    }
    for m in data["matches"] + data["knockout_matches"]:
        if m["id"] == match_id:
            m["prediction"] = new_pred
            save_data(data)
            return RedirectResponse(url="/admin", status_code=303)
    raise HTTPException(404, "Match not found")

@app.post("/admin/delete_match/{match_id}", dependencies=[Depends(verify_admin)])
def delete_match(match_id: int):
    data = load_data()
    data["matches"] = [m for m in data["matches"] if m["id"] != match_id]
    data["knockout_matches"] = [m for m in data["knockout_matches"] if m["id"] != match_id]
    save_data(data)
    return RedirectResponse(url="/admin", status_code=303)

# ---------- Edit match page (separate) ----------
@app.get("/admin/edit_match/{match_id}", response_class=HTMLResponse, dependencies=[Depends(verify_admin)])
def edit_match_form(request: Request, match_id: int):
    data = load_data()
    all_matches = data["matches"] + data.get("knockout_matches", [])
    match = next((m for m in all_matches if m["id"] == match_id), None)
    if not match:
        raise HTTPException(404, "Match not found")
    return templates.TemplateResponse("edit_match.html", {"request": request, "match": match})

@app.post("/admin/edit_match/{match_id}", dependencies=[Depends(verify_admin)])
async def update_match_from_form(match_id: int, request: Request):
    data = load_data()
    all_matches = data["matches"] + data.get("knockout_matches", [])
    match = next((m for m in all_matches if m["id"] == match_id), None)
    if not match:
        raise HTTPException(404, "Match not found")
    form = await request.form()
    
    home_goals = int(form.get("home_goals", 0))
    away_goals = int(form.get("away_goals", 0))
    corners_home = int(form.get("corners_home", 0))
    corners_away = int(form.get("corners_away", 0))
    fouls_home = int(form.get("fouls_home", 0))
    fouls_away = int(form.get("fouls_away", 0))
    yellow_home = int(form.get("yellow_home", 0))
    yellow_away = int(form.get("yellow_away", 0))
    red_home = int(form.get("red_home", 0))
    red_away = int(form.get("red_away", 0))
    both_teams_score = form.get("both_teams_score") == "true"
    over_2_5 = form.get("over_2_5") == "true"
    
    match["prediction"] = {
        "winner": form.get("winner", "TBD"),
        "home_goals": home_goals,
        "away_goals": away_goals,
        "total_goals": home_goals + away_goals,
        "both_teams_score": both_teams_score,
        "over_2_5": over_2_5,
        "corners": {"home": corners_home, "away": corners_away, "total": corners_home + corners_away},
        "fouls": {"home": fouls_home, "away": fouls_away, "total": fouls_home + fouls_away},
        "yellow_cards": {"home": yellow_home, "away": yellow_away, "total": yellow_home + yellow_away},
        "red_cards": {"home": red_home, "away": red_away, "total": red_home + red_away}
    }
    save_data(data)
    return RedirectResponse(url="/admin", status_code=303)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
