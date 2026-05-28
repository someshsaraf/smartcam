from flask import Blueprint, jsonify
from .onvif_discovery import auto_discover

camera_bp = Blueprint("camera_bp", __name__)

@camera_bp.route("/cameras/auto_discover", methods=["GET"])
def discover():
    cameras = auto_discover()
    return jsonify({
        "success": True,
        "count": len(cameras),
        "cameras": cameras
    })
