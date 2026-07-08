"""Seed DEMO data for AI Home Sentinel. Everything created here has is_demo=True.
Real data from the Pi agent (POST /api/pi/*) will be is_demo=False and mix in naturally."""
from datetime import datetime, timezone, timedelta
import uuid

CAM_IMG = {
    "porch": "https://images.unsplash.com/photo-1549109926-58f039549485?crop=entropy&cs=srgb&fm=jpg&ixid=M3w3NTY2Njd8MHwxfHNlYXJjaHwxfHxob21lJTIwZXh0ZXJpb3IlMjBzZWN1cml0eSUyMGNhbWVyYSUyMHZpZXd8ZW58MHx8fHwxNzgzNTA4OTExfDA&ixlib=rb-4.1.0&q=85",
    "driveway": "https://images.unsplash.com/photo-1589935447067-5531094415d1?crop=entropy&cs=srgb&fm=jpg&ixid=M3w3NTY2Njd8MHwxfHNlYXJjaHwyfHxob21lJTIwZXh0ZXJpb3IlMjBzZWN1cml0eSUyMGNhbWVyYSUyMHZpZXd8ZW58MHx8fHwxNzgzNTA4OTExfDA&ixlib=rb-4.1.0&q=85",
    "backyard": "https://images.unsplash.com/photo-1585206031650-9e9a7c87dcfe?crop=entropy&cs=srgb&fm=jpg&ixid=M3w3NTY2Njd8MHwxfHNlYXJjaHwzfHxob21lJTIwZXh0ZXJpb3IlMjBzZWN1cml0eSUyMGNhbWVyYSUyMHZpZXd8ZW58MHx8fHwxNzgzNTA4OTExfDA&ixlib=rb-4.1.0&q=85",
    "garage": "https://images.unsplash.com/photo-1590613607026-15c463e30ca5?crop=entropy&cs=srgb&fm=jpg&ixid=M3w3NTY2Njd8MHwxfHNlYXJjaHw0fHxob21lJTIwZXh0ZXJpb3IlMjBzZWN1cml0eSUyMGNhbWVyYSUyMHZpZXd8ZW58MHx8fHwxNzgzNTA4OTExfDA&ixlib=rb-4.1.0&q=85",
}
PERSON_IMG = {
    "unknown": "https://images.unsplash.com/photo-1587397845856-e6cf49176c70?crop=entropy&cs=srgb&fm=jpg&ixid=M3w4NjAzNTl8MHwxfHNlYXJjaHw0fHxwZXJzb24lMjBwb3J0cmFpdCUyMGxvb2tpbmclMjBhdCUyMGNhbWVyYXxlbnwwfHx8fDE3ODM1MDg5MTF8MA&ixlib=rb-4.1.0&q=85",
    "jane": "https://images.unsplash.com/photo-1506863530036-1efeddceb993?crop=entropy&cs=srgb&fm=jpg&ixid=M3w4NjAzNTl8MHwxfHNlYXJjaHwyfHxwZXJzb24lMjBwb3J0cmFpdCUyMGxvb2tpbmclMjBhdCUyMGNhbWVyYXxlbnwwfHx8fDE3ODM1MDg5MTF8MA&ixlib=rb-4.1.0&q=85",
    "mark": "https://images.unsplash.com/photo-1500648767791-00dcc994a43e?crop=entropy&cs=srgb&fm=jpg&ixid=M3w4NjAzNTl8MHwxfHNlYXJjaHwxfHxwZXJzb24lMjBwb3J0cmFpdCUyMGxvb2tpbmclMjBhdCUyMGNhbWVyYXxlbnwwfHx8fDE3ODM1MDg5MTF8MA&ixlib=rb-4.1.0&q=85",
}
PKG_IMG = "https://images.unsplash.com/photo-1691517534724-3732c7621407?crop=entropy&cs=srgb&fm=jpg&ixid=M3w4NjA1NDh8MHwxfHNlYXJjaHwyfHxwYWNrYWdlJTIwYXQlMjBmcm9udCUyMGRvb3J8ZW58MHx8fHwxNzgzNTA4OTExfDA&ixlib=rb-4.1.0&q=85"


def _iso(dt):
    return dt.isoformat()


def build_seed():
    now = datetime.now(timezone.utc)

    cams = [
        {"id": "cam-porch", "name": "Front Porch", "location": "Front entrance", "status": "online", "thumbnail_url": CAM_IMG["porch"], "stream_url": None, "is_demo": True},
        {"id": "cam-driveway", "name": "Driveway", "location": "Driveway / street", "status": "online", "thumbnail_url": CAM_IMG["driveway"], "stream_url": None, "is_demo": True},
        {"id": "cam-backyard", "name": "Backyard", "location": "Rear yard", "status": "online", "thumbnail_url": CAM_IMG["backyard"], "stream_url": None, "is_demo": True},
        {"id": "cam-garage", "name": "Garage", "location": "Side garage", "status": "offline", "thumbnail_url": CAM_IMG["garage"], "stream_url": None, "is_demo": True},
    ]

    people = [
        {"id": "p-jane", "name": "Jane Doe", "relationship": "Spouse", "status": "trusted", "photo_url": PERSON_IMG["jane"], "encodings_count": 8, "recognition_count": 142, "last_seen": _iso(now - timedelta(minutes=22)), "notes": "Primary resident.", "is_demo": True, "created_at": _iso(now - timedelta(days=40))},
        {"id": "p-mark", "name": "Mark Reyes", "relationship": "Roommate", "status": "trusted", "photo_url": PERSON_IMG["mark"], "encodings_count": 6, "recognition_count": 88, "last_seen": _iso(now - timedelta(hours=3)), "notes": "", "is_demo": True, "created_at": _iso(now - timedelta(days=30))},
        {"id": "p-neighbor", "name": "Neighbor (Tom)", "relationship": "Neighbor", "status": "normal", "photo_url": None, "encodings_count": 3, "recognition_count": 12, "last_seen": _iso(now - timedelta(days=1)), "notes": "Occasionally checks mail.", "is_demo": True, "created_at": _iso(now - timedelta(days=20))},
    ]

    events = [
        {"id": "ev-1", "timestamp": _iso(now - timedelta(minutes=4)), "camera_id": "cam-porch", "camera_name": "Front Porch", "type": "unknown_person", "person_id": None, "person_name": None, "known": False, "objects": ["person", "backpack"], "confidence": 0.91, "importance": "high", "ai_interpretation": "An unrecognized individual lingered near the front door for ~40s without ringing the bell.", "ai_summary": "Unknown person at Front Porch, no match to enrolled faces.", "tags": ["unknown", "front-door", "loitering"], "thumbnail_url": PERSON_IMG["unknown"], "clip_url": None, "saved": True, "is_demo": True},
        {"id": "ev-2", "timestamp": _iso(now - timedelta(minutes=18)), "camera_id": "cam-porch", "camera_name": "Front Porch", "type": "object", "person_id": None, "person_name": None, "known": False, "objects": ["package", "cardboard box"], "confidence": 0.87, "importance": "medium", "ai_interpretation": "A parcel was left at the doorstep, consistent with a delivery drop-off.", "ai_summary": "Package delivered to Front Porch.", "tags": ["package", "delivery"], "thumbnail_url": PKG_IMG, "clip_url": None, "saved": True, "is_demo": True},
        {"id": "ev-3", "timestamp": _iso(now - timedelta(minutes=22)), "camera_id": "cam-driveway", "camera_name": "Driveway", "type": "person", "person_id": "p-jane", "person_name": "Jane Doe", "known": True, "objects": ["person", "car"], "confidence": 0.98, "importance": "low", "ai_interpretation": "Jane arrived home and parked in the driveway.", "ai_summary": "Jane Doe (trusted) recognized at Driveway.", "tags": ["known", "arrival", "jane"], "thumbnail_url": PERSON_IMG["jane"], "clip_url": None, "saved": False, "is_demo": True},
        {"id": "ev-4", "timestamp": _iso(now - timedelta(hours=2, minutes=10)), "camera_id": "cam-backyard", "camera_name": "Backyard", "type": "motion", "person_id": None, "person_name": None, "known": False, "objects": ["animal"], "confidence": 0.64, "importance": "low", "ai_interpretation": "Small motion likely caused by an animal; no human detected.", "ai_summary": "Minor motion at Backyard, probable animal.", "tags": ["motion", "animal"], "thumbnail_url": CAM_IMG["backyard"], "clip_url": None, "saved": False, "is_demo": True},
        {"id": "ev-5", "timestamp": _iso(now - timedelta(hours=3)), "camera_id": "cam-driveway", "camera_name": "Driveway", "type": "person", "person_id": "p-mark", "person_name": "Mark Reyes", "known": True, "objects": ["person"], "confidence": 0.95, "importance": "low", "ai_interpretation": "Mark left the house on foot.", "ai_summary": "Mark Reyes (trusted) recognized at Driveway.", "tags": ["known", "departure", "mark"], "thumbnail_url": PERSON_IMG["mark"], "clip_url": None, "saved": False, "is_demo": True},
        {"id": "ev-6", "timestamp": _iso(now - timedelta(hours=14)), "camera_id": "cam-driveway", "camera_name": "Driveway", "type": "object", "person_id": None, "person_name": None, "known": False, "objects": ["truck", "package", "person"], "confidence": 0.89, "importance": "medium", "ai_interpretation": "A UPS truck stopped and a courier dropped a package around the afternoon delivery window.", "ai_summary": "UPS delivery at Driveway.", "tags": ["package", "ups", "delivery"], "thumbnail_url": PKG_IMG, "clip_url": None, "saved": True, "is_demo": True},
        {"id": "ev-7", "timestamp": _iso(now - timedelta(days=1, hours=1)), "camera_id": "cam-porch", "camera_name": "Front Porch", "type": "unknown_person", "person_id": None, "person_name": None, "known": False, "objects": ["person"], "confidence": 0.78, "importance": "critical", "ai_interpretation": "Unknown person approached the door at 11:42 PM, outside normal activity hours.", "ai_summary": "Late-night unknown person at Front Porch (after 11 PM).", "tags": ["unknown", "night", "after-hours"], "thumbnail_url": PERSON_IMG["unknown"], "clip_url": None, "saved": True, "is_demo": True},
    ]

    segments = []
    seg_defs = [
        ("cam-porch", "Front Porch", 1, ["Jane Doe"], ["person", "car"], "Quiet period. Jane arrived home once; no anomalies.", ["quiet", "known"], ["ev-3"]),
        ("cam-porch", "Front Porch", 2, [], ["package"], "Package delivery drop-off observed; courier left within a minute.", ["package", "delivery"], ["ev-2"]),
        ("cam-porch", "Front Porch", 5, [], ["person"], "Unknown individual near door at night, elevated attention.", ["unknown", "night"], ["ev-7"]),
        ("cam-driveway", "Driveway", 3, ["Mark Reyes"], ["person", "car"], "Routine coming/going of household members.", ["routine", "known"], ["ev-5"]),
        ("cam-driveway", "Driveway", 14, [], ["truck", "package"], "Afternoon UPS delivery captured on the driveway.", ["ups", "package"], ["ev-6"]),
        ("cam-backyard", "Backyard", 2, [], ["animal"], "Low-level motion, likely wildlife. No persons detected.", ["animal", "quiet"], ["ev-4"]),
    ]
    for i, (cid, cname, hrs_ago, ppl, objs, summ, tags, links) in enumerate(seg_defs):
        end = now - timedelta(hours=hrs_ago)
        start = end - timedelta(minutes=30)
        segments.append({
            "id": f"seg-{i+1}", "camera_id": cid, "camera_name": cname,
            "start_time": _iso(start), "end_time": _iso(end), "length_minutes": 30,
            "ai_summary": summ, "tags": tags, "people": ppl, "objects": objs,
            "linked_event_ids": links, "thumbnail_url": CAM_IMG[cid.split("-")[1]], "is_demo": True,
        })

    rules = [
        {"id": "r-1", "name": "Unknown person after 11 PM", "description": "Alert when an unrecognized face is detected between 11 PM and 6 AM.", "trigger_type": "unknown_after_hours", "active": True, "severity": "critical", "schedule": "23:00-06:00", "mode": None, "triggered_count": 1, "last_triggered": _iso(now - timedelta(days=1, hours=1)), "is_demo": True},
        {"id": "r-2", "name": "Package arrival", "description": "Notify when a package/parcel object appears at the porch.", "trigger_type": "package_arrival", "active": True, "severity": "medium", "schedule": None, "mode": None, "triggered_count": 2, "last_triggered": _iso(now - timedelta(minutes=18)), "is_demo": True},
        {"id": "r-3", "name": "Person at door > 60 sec", "description": "Flag loitering when a person stays at the door longer than 60 seconds.", "trigger_type": "loitering", "active": True, "severity": "high", "schedule": None, "mode": None, "triggered_count": 1, "last_triggered": _iso(now - timedelta(minutes=4)), "is_demo": True},
        {"id": "r-4", "name": "Expect UPS 3–5 PM", "description": "Learned routine: deliveries commonly occur weekday afternoons. Suppress low-severity alerts in window.", "trigger_type": "scheduled_expectation", "active": True, "severity": "low", "schedule": "15:00-17:00", "mode": None, "triggered_count": 0, "last_triggered": None, "is_demo": True},
        {"id": "r-5", "name": "Door opens in Away mode", "description": "Critical alert if entry motion is detected while system is in Away mode.", "trigger_type": "mode_violation", "active": False, "severity": "critical", "schedule": None, "mode": "away", "triggered_count": 0, "last_triggered": None, "is_demo": True},
    ]

    settings = {
        "id": "system", "mode": "home", "processing_mode": "hybrid",
        "dvr_segment_minutes": 30, "dvr_retention_hours": 48,
        "recognition_enabled": True, "mic_enabled": False, "motion_gating": True,
        "inference_fps": 5, "privacy_cloud_upload": True, "updated_at": _iso(now),
    }

    return {"cameras": cams, "people": people, "events": events, "segments": segments, "rules": rules, "settings": settings}
