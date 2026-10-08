#!/usr/bin/env python3
"""3D Viewer Server for SDR Receiver Project
Generates a 3D viewer showing the PCB with DXF and GLTF models
"""

import os
import json
import http.server
import socketserver
import threading
import webbrowser
from pathlib import Path

class ViewerServer:
    """3D viewer server for the SDR receiver PCB."""
    
    def __init__(self, project_dir):
        self.project_dir = Path(project_dir)
        self.out_dir = self.project_dir / "out"
        self.viewer_dir = self.project_dir / "view"
        
    def generate_viewer_html(self):
        """Generate the HTML file for the 3D viewer."""
        html_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>SDR Receiver 3D Viewer</title>
    <style>
        body { margin: 0; font-family: Arial, sans-serif; background: #1a1a2e; color: #fff; }
        h1 { text-align: center; padding: 20px; }
        .container { max-width: 1200px; margin: 0 auto; padding: 20px; }
        .viewer-panel { display: flex; gap: 20px; flex-wrap: wrap; justify-content: center; }
        .viewer { border: 1px solid #ccc; border-radius: 8px; background: #2d2d3e; padding: 10px; }
        .dxf-viewer { width: 500px; height: 400px; }
        .glb-viewer { width: 500px; height: 400px; }
        .controls { margin-top: 10px; text-align: center; }
    </style>
</head>
<body>
    <h1>SDR Direct-Conversion Receiver - 3D Viewer</h1>
    <div class="container">
        <div class="viewer-panel">
            <div class="viewer">
                <h3>DXF Viewer</h3>
                <iframe class="dxf-viewer" src="dxf_viewer.html" frameborder="0"></iframe>
                <div class="controls">DXF output</div>
            </div>
            <div class="viewer">
                <h3>3D Model (GLTF)</h3>
                <iframe class="glb-viewer" src="glb_viewer.html" frameborder="0"></iframe>
                <div class="controls">3D model viewer</div>
            </div>
        </div>
    </div>
    <script>
        // Initialize 3D viewers on load
        document.addEventListener('DOMContentLoaded', function() {
            console.log('3D Viewer initialized');
        });
    </script>
</body>
</html>
"""
        return html_content
    
    def start_server(self, port=8080):
        """Start the HTTP server for 3D viewing."""
        handler = http.server.SimpleHTTPRequestHandler
        with socketserver.TCPServer(("", port), handler) as httpd:
            print(f"Server started at http://localhost:{port}")
            print("Opening browser...")
            webbrowser.open(f'http://localhost:{port}')
            try:
                httpd.serve_forever()
            except KeyboardInterrupt:
                print("\nServer stopped")

def main():
    import socketserver
    project_dir = "/home/mirrage/Desktop/кристалл-радио"
    viewer = ViewerServer(project_dir)
    
    # Generate viewer HTML
    html = viewer.generate_viewer_html()
    with open(viewer.viewer_dir / "index.html", 'w') as f:
        f.write(html)
    print("Generated viewer HTML")
    
    # Start server in background thread
    server_thread = threading.Thread(target=viewer.start_server, daemon=True)
    server_thread.start()
    
    # Open browser
    webbrowser.open('http://localhost:8080')
    print("Browser opened at http://localhost:8080")
    
    # Keep the script running
    import time
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nViewer stopped")

if __name__ == "__main__":
    main()