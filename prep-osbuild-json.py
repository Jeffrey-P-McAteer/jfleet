#!/usr/bin/env python3
import toml
import json
import sys
import os

BLUEPRINT = sys.argv[1]
OUTPUT_JSON = sys.argv[2]

data = toml.load(BLUEPRINT)

manifest = {
    "schema": 1,
    "name": data.get("name", "custom-image"),
    "description": data.get("description", ""),
    "pipelines": [
        {
            "name": "org.osbuild.rpm",
            "stages": [
                {"name": "org.osbuild.rpm",
                 "options": {
                     "packages": [pkg["name"] for pkg in data.get("packages", [])],
                     "repos": [{"id": "base", "baseurl": "file:///path/to/local/rpms"}]
                 }}
            ]
        },
        {
            "name": "org.osbuild.files",
            "stages": [
                {"name": "org.osbuild.files",
                 "options": {
                     "files": [
                         {
                             "source": f"file://{f['source']}",
                             "destination": f["path"],
                             "mode": f.get("mode", "0644")
                         } for f in data.get("files", [])
                     ]
                 }}
            ]
        },
        {
            "name": "org.osbuild.systemd",
            "stages": [
                {"name": "org.osbuild.systemd",
                 "options": {
                     "units": [
                         {"name": svc["name"], "enabled": svc.get("enabled", False)}
                         for svc in data.get("services", [])
                     ]
                 }}
            ]
        }
    ],
    "artifacts": [
        {"type": "rootfs", "filename": "rootfs.tar"},
        {"type": "raw", "filename": "image.raw"},
        {"type": "qcow2", "filename": "image.qcow2"}
    ]
}

with open(OUTPUT_JSON, "w") as f:
    json.dump(manifest, f, indent=2)
