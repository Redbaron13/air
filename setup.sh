#!/bin/bash

# BAM Media Pipeline Scaffold Script
# ----------------------------------

PROJECT_ROOT="bam-media-pipeline"

echo "Scaffolding directory structure for $PROJECT_ROOT..."

# Create core directories and nested storage paths
mkdir -p "$PROJECT_ROOT/sql"
mkdir -p "$PROJECT_ROOT/server"
mkdir -p "$PROJECT_ROOT/scripts"
mkdir -p "$PROJECT_ROOT/static/js"
mkdir -p "$PROJECT_ROOT/storage/onedrive_mount"
mkdir -p "$PROJECT_ROOT/storage/originals"
mkdir -p "$PROJECT_ROOT/storage/active"
mkdir -p "$PROJECT_ROOT/storage/exports"

# Generate root configuration files
touch "$PROJECT_ROOT/.env"
touch "$PROJECT_ROOT/docker-compose.yml"

# Generate SQL schema file
touch "$PROJECT_ROOT/sql/init_schema.sql"

# Generate Python server files
touch "$PROJECT_ROOT/server/Dockerfile"
touch "$PROJECT_ROOT/server/requirements.txt"
touch "$PROJECT_ROOT/server/app.py"

# Generate worker scripts and Node configurations
touch "$PROJECT_ROOT/scripts/Dockerfile"
touch "$PROJECT_ROOT/scripts/package.json"
touch "$PROJECT_ROOT/scripts/derivative_builder.js"
touch "$PROJECT_ROOT/scripts/vision_orchestrator.py"
touch "$PROJECT_ROOT/scripts/cloud_fallback_worker.py"
touch "$PROJECT_ROOT/scripts/emit_manifest.py"

# Generate frontend GUI files
touch "$PROJECT_ROOT/static/index.html"
touch "$PROJECT_ROOT/static/js/app.js"

echo "Success! The directory tree and file placeholders have been generated."
echo "You can now navigate into the project using: cd $PROJECT_ROOT"
