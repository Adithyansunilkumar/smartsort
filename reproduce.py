import os
from pathlib import Path
from app.services.folder_access import FolderAccessService
from app.services.protected_paths import ProtectedPathService
from app.organization.planner import OrganizationPlanner
import tempfile

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    folder_service = FolderAccessService()
    folder_service.set_authorized_root(root)
    protected_service = ProtectedPathService(folder_service)
    
    # Mock files data with an image file inside Images
    files_data = [
        {
            "relative_path": "Images/old_file.jpg",
            "category": "image",
            "name": "old_file.jpg"
        },
        {
            "relative_path": "177981430036.png",
            "category": "image",
            "name": "177981430036.png"
        }
    ]
    
    planner = OrganizationPlanner(folder_service, protected_service)
    plan = planner.generate_plan("test_scan_id", files_data)
    
    creates = [op for op in plan.operations if op.type == "CREATE_FOLDER"]
    moves = [op for op in plan.operations if op.type == "MOVE"]
    renames = [op for op in plan.operations if op.type == "RENAME"]
    
    print(f"CREATE_FOLDER count: {len(creates)}")
    for c in creates:
        print(f"  Destination: {c.destination}")
    print(f"MOVE count: {len(moves)}")
    print(f"RENAME count: {len(renames)}")
    for r in renames:
        print(f"  Destination: {r.destination}")
