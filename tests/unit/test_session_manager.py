import datetime
import json
import os
import time
import uuid

from fastapi.testclient import TestClient

from src.web.backend.app import app
from src.core.sessions import (
    SessionManager,
    delete_session,
    enforce_storage_retention_policy,
    format_session_dir_name,
    get_most_recent_version_num,
    get_session,
    load_retention_config,
    load_session,
    purge_session_except_active,
    purge_version_heavy_assets,
    rename_session_folder,
    resolve_session_dir,
    sanitize_folder_name_part,
    save_session_atomic,
)

client = TestClient(app)


def test_sanitize_folder_name_part():
    assert sanitize_folder_name_part("Desk Cable Holder!") == "desk_cable_holder"
    assert sanitize_folder_name_part("___Model__Name___") == "model_name"
    assert sanitize_folder_name_part("") == "model"
    assert sanitize_folder_name_part("A" * 60) == "a" * 50


def test_format_session_dir_name():
    session_id = "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d"
    name = format_session_dir_name("Desk Cable Holder", session_id, timestamp="20261017_104500")
    assert name == "20261017_104500_desk_cable_holder_9b1deb4d"


def test_resolve_session_dir_and_rename(tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()

    full_uuid = "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d"
    short8 = "9b1deb4d"
    init_folder = f"20261017_104500_generating_{short8}"
    sess_dir = sessions_dir / init_folder
    sess_dir.mkdir()

    session_data = {
        "id": full_uuid,
        "folder_name": init_folder,
        "title": "Generating...",
        "history": [],
        "versions": 1,
        "last_updated": datetime.datetime.now().isoformat(),
    }
    save_session_atomic(str(sess_dir), session_data, sessions_dir=str(sessions_dir))

    # 1. Resolve by full UUID
    resolved = resolve_session_dir(full_uuid, sessions_dir=str(sessions_dir))
    assert resolved == str(sess_dir)

    # 2. Resolve by short8
    resolved_short = resolve_session_dir(short8, sessions_dir=str(sessions_dir))
    assert resolved_short == str(sess_dir)

    # 3. Resolve by folder name
    resolved_folder = resolve_session_dir(init_folder, sessions_dir=str(sessions_dir))
    assert resolved_folder == str(sess_dir)

    # 4. Rename folder to model name
    new_dir = rename_session_folder(full_uuid, "Desk Cable Holder", sessions_dir=str(sessions_dir))
    assert new_dir is not None
    assert os.path.basename(new_dir) == f"20261017_104500_desk_cable_holder_{short8}"
    assert os.path.exists(new_dir)
    assert not os.path.exists(sess_dir)

    # 5. Resolve after rename still finds the folder by UUID
    resolved_after = resolve_session_dir(full_uuid, sessions_dir=str(sessions_dir))
    assert resolved_after == new_dir

    # 6. SessionManager static methods mirror module functions
    assert SessionManager.resolve_session_dir(full_uuid, sessions_dir=str(sessions_dir)) == new_dir


def test_load_and_save_session_with_hybrid_naming(tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()

    full_uuid = str(uuid.uuid4())
    short8 = full_uuid.replace("-", "")[:8]
    folder_name = f"20261018_120000_gear_bearing_{short8}"
    s_dir = sessions_dir / folder_name
    s_dir.mkdir()

    data = {
        "id": full_uuid,
        "title": "Gear Bearing",
        "last_updated": "2026-10-18T12:00:00",
    }
    save_session_atomic(full_uuid, data, sessions_dir=str(sessions_dir))

    # Load by UUID
    loaded = load_session(full_uuid, sessions_dir=str(sessions_dir))
    assert loaded is not None
    assert loaded["id"] == full_uuid
    assert loaded["title"] == "Gear Bearing"

    # Get session
    got = get_session(full_uuid, sessions_dir=str(sessions_dir))
    assert got is not None
    assert got["id"] == full_uuid

    # Delete session
    del_res = delete_session(full_uuid, sessions_dir=str(sessions_dir))
    assert del_res is True
    assert not os.path.exists(s_dir)
    assert load_session(full_uuid, sessions_dir=str(sessions_dir)) is None


def test_retention_config_loading(tmp_path):
    cfg_dir = tmp_path / "configs"
    cfg_dir.mkdir()
    cfg_file = cfg_dir / "agent_config.json"

    # Test defaults when file missing
    cfg_default = load_retention_config(configs_dir=str(cfg_dir))
    assert cfg_default["auto_purge_enabled"] is True
    assert cfg_default["keep_all_versions_sessions"] == 15
    assert cfg_default["keep_latest_version_sessions"] == 30

    # Test custom configuration
    cfg_file.write_text(
        json.dumps(
            {
                "session_retention": {
                    "auto_purge_enabled": True,
                    "keep_all_versions_sessions": 5,
                    "keep_latest_version_sessions": 10,
                }
            }
        ),
        encoding="utf-8",
    )
    cfg_custom = load_retention_config(configs_dir=str(cfg_dir))
    assert cfg_custom["keep_all_versions_sessions"] == 5
    assert cfg_custom["keep_latest_version_sessions"] == 10


def test_purge_version_heavy_assets_preserves_scad(tmp_path):
    v_dir = tmp_path / "v1"
    v_dir.mkdir()

    stl = v_dir / "model.stl"
    stl.write_text("solid")
    mf = v_dir / "model.3mf"
    mf.write_text("3mf")
    png = v_dir / "preview.png"
    png.write_text("png")
    scad = v_dir / "model.scad"
    scad.write_text("cube(10);")

    purged = purge_version_heavy_assets(str(v_dir))
    assert len(purged) == 3
    assert not stl.exists()
    assert not mf.exists()
    assert not png.exists()
    assert scad.exists()


def test_get_most_recent_version_num_and_purge_except_active(tmp_path):
    s_dir = tmp_path / "session_multi_v"
    s_dir.mkdir()

    v1_dir = s_dir / "v1"
    v1_dir.mkdir()
    (v1_dir / "model.scad").write_text("cube(10);")
    stl1 = v1_dir / "model.stl"
    stl1.write_text("stl1")

    v2_dir = s_dir / "v2"
    v2_dir.mkdir()
    (v2_dir / "model.scad").write_text("cube(20);")
    stl2 = v2_dir / "model.stl"
    stl2.write_text("stl2")

    # When last_active_version is specified as 1
    session_data = {"id": "session_multi_v", "last_active_version": 1}
    assert get_most_recent_version_num(str(s_dir), session_data) == 1

    # Purge except active (v1 is active)
    purge_info = purge_session_except_active(str(s_dir), session_data)
    assert purge_info["keep_version"] == 1
    assert stl1.exists()
    assert not stl2.exists()
    assert (v2_dir / "model.scad").exists()
    assert session_data.get("purged_partial") is True


def test_enforce_storage_retention_policy_3_tiers(tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    cfg_dir = tmp_path / "configs"
    cfg_dir.mkdir()
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    snapshots_dir = tmp_path / "snapshots"
    snapshots_dir.mkdir()

    cfg_file = cfg_dir / "agent_config.json"
    cfg_file.write_text(
        json.dumps(
            {
                "session_retention": {
                    "auto_purge_enabled": True,
                    "keep_all_versions_sessions": 15,
                    "keep_latest_version_sessions": 30,
                }
            }
        ),
        encoding="utf-8",
    )

    # Create 35 sessions, #1 (newest) to #35 (oldest)
    session_dirs = []
    base_time = datetime.datetime(2026, 10, 20, 12, 0, 0)

    for i in range(1, 36):
        # i=1 is newest, i=35 is oldest
        dt = base_time - datetime.timedelta(hours=i)
        ts_str = dt.strftime("%Y%m%d_%H%M%S")
        sess_uuid = str(uuid.uuid4())
        short_id = sess_uuid.replace("-", "")[:8]
        dir_name = f"{ts_str}_model_{i}_{short_id}"
        s_path = sessions_dir / dir_name
        s_path.mkdir()

        # Create v1 and v2 with .stl and .scad
        v1 = s_path / "v1"
        v1.mkdir()
        (v1 / "design.scad").write_text(f"// v1 code {i}")
        (v1 / "design.stl").write_text(f"// v1 stl {i}")
        (v1 / "preview.png").write_text("png")

        v2 = s_path / "v2"
        v2.mkdir()
        (v2 / "design.scad").write_text(f"// v2 code {i}")
        (v2 / "design.stl").write_text(f"// v2 stl {i}")
        (v2 / "preview.png").write_text("png")

        s_data = {
            "id": sess_uuid,
            "folder_name": dir_name,
            "title": f"Model {i}",
            "versions": 2,
            "last_updated": dt.isoformat(),
            "last_active_version": 2,
        }
        (s_path / "session.json").write_text(json.dumps(s_data), encoding="utf-8")
        session_dirs.append((i, s_path, sess_uuid))

    # Add stale temp folders (one older than 1 hr, one fresh)
    stale_tmp = tmp_dir / "job_stale_123"
    stale_tmp.mkdir()
    (stale_tmp / "junk.txt").write_text("old")
    # Set mtime back 2 hours
    old_time = time.time() - 7200
    os.utime(str(stale_tmp), (old_time, old_time))

    fresh_tmp = tmp_dir / "job_fresh_456"
    fresh_tmp.mkdir()

    # Add 8 snapshot jpg files
    for k in range(8):
        snap = snapshots_dir / f"snap_{k}.jpg"
        snap.write_text("camera")
        snap_mtime = time.time() - (10 - k) * 60
        os.utime(str(snap), (snap_mtime, snap_mtime))

    # Run retention policy
    result = enforce_storage_retention_policy(
        sessions_dir=str(sessions_dir),
        configs_dir=str(cfg_dir),
        tmp_dir=str(tmp_dir),
        snapshots_dir=str(snapshots_dir),
    )

    assert result["tier1_kept_all"] == 15
    assert result["tier2_kept_active"] == 15
    assert result["tier3_purged_all"] == 5

    # Verify Tier 1 (Sessions 1 to 15): keep v1 and v2 stl
    for i in range(1, 16):
        s_path = session_dirs[i - 1][1]
        assert (s_path / "v1" / "design.stl").exists(), f"Session {i} v1 stl should exist"
        assert (s_path / "v2" / "design.stl").exists(), f"Session {i} v2 stl should exist"

    # Verify Tier 2 (Sessions 16 to 30): keep active (v2) only; v1 stl purged
    for i in range(16, 31):
        s_path = session_dirs[i - 1][1]
        assert not (s_path / "v1" / "design.stl").exists(), f"Session {i} v1 stl should be purged"
        assert (s_path / "v2" / "design.stl").exists(), f"Session {i} v2 stl should be kept"
        assert (s_path / "v1" / "design.scad").exists()
        assert (s_path / "v2" / "design.scad").exists()

    # Verify Tier 3 (Sessions 31 to 35): all heavy files purged, scad + session.json preserved
    for i in range(31, 36):
        s_path = session_dirs[i - 1][1]
        assert not (s_path / "v1" / "design.stl").exists(), f"Session {i} v1 stl should be purged"
        assert not (s_path / "v2" / "design.stl").exists(), f"Session {i} v2 stl should be purged"
        assert (s_path / "v1" / "design.scad").exists()
        assert (s_path / "v2" / "design.scad").exists()
        assert (s_path / "session.json").exists()

    # Verify stale temp swept
    assert not stale_tmp.exists()
    assert fresh_tmp.exists()

    # Verify snapshots: kept only latest 5
    remaining_snaps = [f for f in os.listdir(snapshots_dir) if f.endswith(".jpg")]
    assert len(remaining_snaps) == 5


def test_rerender_promotion_and_protection(tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    cfg_dir = tmp_path / "configs"
    cfg_dir.mkdir()

    # 1-tier threshold of 2, tier2 threshold of 4 for quick test
    cfg_file = cfg_dir / "agent_config.json"
    cfg_file.write_text(
        json.dumps(
            {
                "session_retention": {
                    "auto_purge_enabled": True,
                    "keep_all_versions_sessions": 2,
                    "keep_latest_version_sessions": 4,
                }
            }
        ),
        encoding="utf-8",
    )

    # Create 5 sessions (Session 1 to 5)
    base_time = datetime.datetime(2026, 10, 20, 10, 0, 0)
    sessions = []
    for i in range(1, 6):
        dt = base_time - datetime.timedelta(hours=i)
        ts_str = dt.strftime("%Y%m%d_%H%M%S")
        sess_uuid = str(uuid.uuid4())
        dir_name = f"{ts_str}_sess_{i}_{sess_uuid[:8]}"
        s_path = sessions_dir / dir_name
        s_path.mkdir()

        v1 = s_path / "v1"
        v1.mkdir()
        (v1 / "model.scad").write_text("cube(5);")
        (v1 / "model.stl").write_text("stl1")

        v2 = s_path / "v2"
        v2.mkdir()
        (v2 / "model.scad").write_text("cube(10);")
        (v2 / "model.stl").write_text("stl2")

        data = {
            "id": sess_uuid,
            "folder_name": dir_name,
            "last_updated": dt.isoformat(),
            "last_active_version": 2,
        }
        (s_path / "session.json").write_text(json.dumps(data), encoding="utf-8")
        sessions.append((i, s_path, sess_uuid))

    # Initial retention: Session 5 is in Tier 3 (purged completely)
    enforce_storage_retention_policy(
        sessions_dir=str(sessions_dir),
        configs_dir=str(cfg_dir),
        tmp_dir=str(tmp_path),
        snapshots_dir=str(tmp_path),
    )

    s5_path = sessions[4][1]
    assert not (s5_path / "v1" / "model.stl").exists()
    assert not (s5_path / "v2" / "model.stl").exists()

    # Simulate re-rendering v1 of Session 5
    (s5_path / "v1" / "model.stl").write_text("re-rendered v1")
    s5_data = json.loads((s5_path / "session.json").read_text(encoding="utf-8"))
    s5_data["last_updated"] = datetime.datetime.now().isoformat()
    s5_data["last_active_version"] = 1
    (s5_path / "session.json").write_text(json.dumps(s5_data), encoding="utf-8")

    # Run retention policy again
    enforce_storage_retention_policy(
        sessions_dir=str(sessions_dir),
        configs_dir=str(cfg_dir),
        tmp_dir=str(tmp_path),
        snapshots_dir=str(tmp_path),
    )

    # Session 5 was promoted to Rank #1 (Tier 1)! Its v1 stl is preserved!
    assert (s5_path / "v1" / "model.stl").exists()


def test_api_session_lifecycle_hybrid_naming(tmp_path, monkeypatch):
    test_ws = str(tmp_path / "created_stls")
    os.makedirs(test_ws, exist_ok=True)
    monkeypatch.setattr("src.web.backend.routes.sessions.WORKSPACE_DIR", test_ws)

    # 1. Create a session via /sessions/new
    res = client.post("/sessions/new", json={"prompt": "practice test"})
    assert res.status_code == 200

    lines = [l for l in res.text.strip().split("\n") if l]
    events = [json.loads(l) for l in lines]
    init_evt = events[0]
    assert init_evt["status"] == "init"
    sess_id = init_evt["session_id"]
    assert sess_id is not None

    complete_evt = events[-1]
    assert complete_evt["status"] == "complete"
    session_obj = complete_evt["session"]
    assert session_obj["id"] == sess_id
    assert "folder_name" in session_obj

    # 2. Verify folder exists on disk with timestamp-first hybrid naming
    folder_name = session_obj["folder_name"]
    assert "practice_cube" in folder_name
    assert folder_name.endswith(sess_id.replace("-", "")[:8])
    assert os.path.isdir(os.path.join(test_ws, folder_name))

    # 3. GET /sessions/{id} works using permanent UUID
    res_get = client.get(f"/sessions/{sess_id}")
    assert res_get.status_code == 200
    got_session = res_get.json()
    assert got_session["id"] == sess_id
    assert got_session["folder_name"] == folder_name

    # 4. GET /sessions lists the session
    res_list = client.get("/sessions")
    assert res_list.status_code == 200
    all_sessions = res_list.json()
    assert any(s["id"] == sess_id for s in all_sessions)

    # 5. Rerender v1 updates last_active_version
    res_rerender = client.post(f"/sessions/{sess_id}/versions/1/rerender")
    assert res_rerender.status_code == 200
    r_lines = [l for l in res_rerender.text.strip().split("\n") if l]
    r_complete = json.loads(r_lines[-1])
    assert r_complete["status"] == "complete"

    # Verify session.json recorded last_active_version
    s_json = json.loads(
        open(os.path.join(test_ws, folder_name, "session.json"), "r", encoding="utf-8").read()
    )
    s_data_version = s_json.get("last_active_version")
    assert s_data_version == 1


def test_purge_preserves_scad_and_cleans_legacy_bat(tmp_path):
    from src.core.sessions import (
        purge_all_session_heavy_assets,
        purge_heavy_assets,
        purge_version_heavy_assets,
        write_version_print_scripts,
    )

    s_dir = tmp_path / "session_bat_test"
    v1_dir = s_dir / "v1"
    v2_dir = s_dir / "v2"
    v1_dir.mkdir(parents=True)
    v2_dir.mkdir(parents=True)

    stl1 = v1_dir / "part_v1.stl"
    stl1.write_text("solid v1 endsolid v1", encoding="utf-8")
    scad1 = v1_dir / "part_v1.scad"
    scad1.write_text("cube(10);", encoding="utf-8")
    legacy_bat1 = v1_dir / "print.bat"
    legacy_bat1.write_text("echo old print", encoding="utf-8")

    res1 = write_version_print_scripts(
        str(v1_dir), str(stl1), requested_colors=["Red", "Blue"], enable_supports=True
    )
    assert res1 == ("", "")
    assert not legacy_bat1.exists()

    stl2 = v2_dir / "part_v2.stl"
    stl2.write_text("solid v2 endsolid v2", encoding="utf-8")
    scad2 = v2_dir / "part_v2.scad"
    scad2.write_text("cube(20);", encoding="utf-8")
    legacy_bat2 = v2_dir / "print.bat"
    legacy_bat2.write_text("echo old print 2", encoding="utf-8")

    res2 = write_version_print_scripts(
        str(v2_dir), str(stl2), requested_colors=[], enable_supports=False
    )
    assert res2 == ("", "")
    assert not legacy_bat2.exists()

    # 1. purge_version_heavy_assets preserves .scad
    purge_version_heavy_assets(str(v1_dir))
    assert not stl1.exists()
    assert scad1.exists()

    # 2. purge_all_session_heavy_assets preserves .scad
    purge_all_session_heavy_assets(str(s_dir))
    assert not stl2.exists()
    assert scad2.exists()

    # 3. purge_heavy_assets preserves .scad
    stl2.write_text("solid v2 endsolid v2", encoding="utf-8")
    purge_heavy_assets(str(s_dir))
    assert not stl2.exists()
    assert scad2.exists()


def test_retention_sweeps_slice_and_validate_tmp_dirs(tmp_path):
    sessions_dir = tmp_path / "sessions"
    sessions_dir.mkdir()
    cfg_dir = tmp_path / "configs"
    cfg_dir.mkdir()
    tmp_dir = tmp_path / "tmp"
    tmp_dir.mkdir()
    snapshots_dir = tmp_path / "snapshots"
    snapshots_dir.mkdir()

    old_time = time.time() - 7200  # 2 hours ago
    for prefix in ("job_old_1", "slice_old_2", "ttp_validate_old_3"):
        d = tmp_dir / prefix
        d.mkdir()
        (d / "artifact.stl").write_text("stale", encoding="utf-8")
        os.utime(str(d), (old_time, old_time))

    for prefix in ("job_fresh_1", "slice_fresh_2", "ttp_validate_fresh_3", "unrelated_old"):
        d = tmp_dir / prefix
        d.mkdir()
        if prefix == "unrelated_old":
            os.utime(str(d), (old_time, old_time))

    res = enforce_storage_retention_policy(
        sessions_dir=str(sessions_dir),
        configs_dir=str(cfg_dir),
        tmp_dir=str(tmp_dir),
        snapshots_dir=str(snapshots_dir),
    )

    assert set(res["swept_tmp"]) == {"job_old_1", "slice_old_2", "ttp_validate_old_3"}
    assert not (tmp_dir / "job_old_1").exists()
    assert not (tmp_dir / "slice_old_2").exists()
    assert not (tmp_dir / "ttp_validate_old_3").exists()
    assert (tmp_dir / "job_fresh_1").exists()
    assert (tmp_dir / "slice_fresh_2").exists()
    assert (tmp_dir / "ttp_validate_fresh_3").exists()
    assert (tmp_dir / "unrelated_old").exists()
