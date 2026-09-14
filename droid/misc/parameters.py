import os
from cv2 import aruco

# Robot Params #
nuc_ip = "128.59.17.233"
robot_ip = "128.59.17.200"
laptop_ip = "128.59.17.239"
sudo_password = os.environ.get("DROID_SUDO_PASSWORD", "")  # configure locally; never commit credentials
robot_type = "fr3"  # 'panda' or 'fr3'
robot_serial_number = "295341-2584010"

# Camera ID's #
hand_camera_id = ""
varied_camera_1_id = ""
varied_camera_2_id = ""

# Charuco Board Params #
CHARUCOBOARD_ROWCOUNT = 9
CHARUCOBOARD_COLCOUNT = 14
CHARUCOBOARD_CHECKER_SIZE = 0.020
CHARUCOBOARD_MARKER_SIZE = 0.016
ARUCO_DICT = aruco.Dictionary_get(aruco.DICT_5X5_100)

# Ubuntu Pro Token (RT PATCH) #
ubuntu_pro_token = ""

# Code Version [DONT CHANGE] #
droid_version = "1.3"
