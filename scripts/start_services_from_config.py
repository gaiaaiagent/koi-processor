#!/usr/bin/env python3
"""
Start KOI services using centralized configuration.
Ensures services always use correct ports.
"""

import json
import os
import subprocess
import time
import requests
from pathlib import Path

# Load service configuration
CONFIG_FILE = Path(__file__).parent.parent / "config" / "services.json"

def load_config():
    """Load service configuration from JSON file."""
    with open(CONFIG_FILE, 'r') as f:
        return json.load(f)

def check_service_health(service):
    """Check if a service is healthy."""
    try:
        url = f"http://{service['host']}:{service['port']}{service.get('health_endpoint', '/')}"
        response = requests.get(url, timeout=2)
        return response.status_code in [200, 404]  # 404 for root endpoints is OK
    except:
        return False

def start_service(name, service):
    """Start a single service."""
    print(f"Starting {service['name']}...")
    
    # Skip PostgreSQL (managed by Docker)
    if 'docker_container' in service:
        print(f"  {service['name']} is managed by Docker")
        return True
    
    # Check if already running
    if check_service_health(service):
        print(f"  ✓ {service['name']} already running on port {service['port']}")
        return True
    
    # Build command
    cmd = service['start_command']
    
    # Add environment variables if needed
    env = os.environ.copy()
    if 'env_var' in service:
        env[service['env_var']] = str(service['port'])
    
    # Start the service
    log_file = f"/opt/projects/koi-processor/logs/{name}.log"
    with open(log_file, 'w') as f:
        subprocess.Popen(
            cmd,
            shell=True,
            stdout=f,
            stderr=f,
            env=env,
            cwd="/opt/projects/koi-processor"
        )
    
    # Wait for service to start
    time.sleep(3)
    
    # Check if started successfully
    if check_service_health(service):
        print(f"  ✓ {service['name']} started on port {service['port']}")
        return True
    else:
        print(f"  ✗ Failed to start {service['name']}")
        return False

def main():
    """Main function to start all services."""
    print("=" * 60)
    print("Starting KOI Pipeline Services")
    print("=" * 60)
    
    config = load_config()
    services = config['services']
    
    # Start services in order
    service_order = [
        'bge_server',
        'event_bridge', 
        'koi_coordinator',
        'mcp_server',
        'content_dashboard'
    ]
    
    success_count = 0
    for name in service_order:
        if name in services:
            if start_service(name, services[name]):
                success_count += 1
    
    print("=" * 60)
    print(f"Started {success_count}/{len(service_order)} services")
    print("\nService URLs:")
    for name in service_order:
        if name in services:
            service = services[name]
            print(f"  {service['name']}: http://localhost:{service['port']}")
    
    print("\nNginx proxy paths:")
    for path, target in config['nginx_proxy_paths'].items():
        print(f"  https://regen.gaiaai.xyz{path} -> {target}")
    
    print("=" * 60)

if __name__ == "__main__":
    main()