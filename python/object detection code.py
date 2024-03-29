from depthai_sdk import OakCamera
from depthai_sdk.visualize.configs import StereoColor
from depthai_sdk.components.stereo_component import WLSLevel
from depthai_sdk.classes.packets import DisparityDepthPacket
import math
import depthai as dai
import cv2
import numpy as np
import threading
import time
import serial


# Définir le port série et le débit en bauds
SERIAL_PORT = 'COM3'  # Assurez-vous que le port est correct
BAUD_RATE = 115200

# Initialiser la connexion série
ser = serial.Serial(SERIAL_PORT, BAUD_RATE)
number = 0	
number_left = 0		
number_right = 0		
number_center = 0
CRITICAL_DISTANCE = float(ser.readline().decode('utf-8').strip()) * 2


# # User-defined constant
# CRITICAL_DISTANCE = 1000  # 1000cm

slc_data = []
def send_serial_command(vibrator):
   ser.write(f"{vibrator}\n".encode('utf-8'))

def adjust_brightness(image, factor=1.5):
    # Assurez-vous que les valeurs ne dépassent pas 255 après l'ajustement
    return np.clip(image * factor, 0, 255).astype(np.uint8)

# Initialize a counter variable
alert_counter = 0

def circular_mask(height, width, center, radius):
    y, x = np.ogrid[:height, :width]
    mask = ((x - center[0]) ** 2 + (y - center[1]) ** 2 <= radius ** 2).astype(np.uint8)
    return mask

def main():
    global slc_data, alert_counter, number, number_left, number_right, number_center, CRITICAL_DISTANCE

    try:
        with OakCamera() as oak:
            left = oak.create_camera('left')
            right = oak.create_camera('right')
            stereo = oak.create_stereo(left=left, right=right)
            #oak.visualize([stereo.out.depth], fps=True, scale=2/3)

            config = stereo.node.initialConfig.get()
            config.postProcessing.brightnessFilter.minBrightness = 0
            config.postProcessing.brightnessFilter.maxBrightness = 255
            stereo.node.initialConfig.set(config)
            stereo.config_postprocessing(colorize=StereoColor.RGBD, colormap=cv2.COLORMAP_BONE)
            stereo.config_stereo(confidence=100, lr_check=True, extended=True)

            oak.visualize([stereo], fps=True, callback=cb)

            slc = oak.pipeline.create(dai.node.SpatialLocationCalculator)
            for x in range(15):
                for y in range(9):
                    config = dai.SpatialLocationCalculatorConfigData()
                    config.depthThresholds.lowerThreshold = 200
                    config.depthThresholds.upperThreshold = 3000
                    config.roi = dai.Rect(dai.Point2f((x+0.5)*0.0625, (y+0.5)*0.1), dai.Point2f((x+1.5)*0.0625, (y+1.5)*0.1))
                    config.calculationAlgorithm = dai.SpatialLocationCalculatorAlgorithm.MEDIAN
                    slc.initialConfig.addROI(config)

            stereo.depth.link(slc.inputDepth)

            slc_out = oak.pipeline.create(dai.node.XLinkOut)
            slc_out.setStreamName('slc')
            slc.out.link(slc_out.input)

            oak.start()  # Start the pipeline

            q = oak.device.getOutputQueue('slc')  # Create output queue after calling start()

            while oak.running():
                if q.has():
                    slc_data = q.get().getSpatialLocations()

                oak.poll()

    except Exception as e:
        print(f"An error occurred: {e}")
    finally:
        ser.close()

def cb(packet: DisparityDepthPacket):
    global slc_data, alert_counter, number, number_left, number_right, number_center, CRITICAL_DISTANCE
    CRITICAL_DISTANCE = float(ser.readline().decode('utf-8').strip()) * 2
    # print(CRITICAL_DISTANCE,"cm")
    # Initialize the message variable
    message = ""
    var = 0
    var2 = 0
    depth_frame_color = packet.visualizer.draw(packet.frame)

    h, w = depth_frame_color.shape[:2]
    center = (w // 2, h // 2)
    radius = 200

    mask = circular_mask(h, w, center, radius)
    mask_left = mask[:, :w // 2]
    mask_right = mask[:, w // 2:]

    mask_left = cv2.GaussianBlur(mask_left, (0, 0), sigmaX=10, sigmaY=10)
    mask_right = cv2.GaussianBlur(mask_right, (0, 0), sigmaX=10, sigmaY=10)

    if len(depth_frame_color.shape) == 3:
        depth_frame_color_left = cv2.bitwise_and(depth_frame_color[:, :w // 2, :], depth_frame_color[:, :w // 2, :], mask=mask_left)
        depth_frame_color_right = cv2.bitwise_and(depth_frame_color[:, w // 2:, :], depth_frame_color[:, w // 2:, :], mask=mask_right)
    else:
        depth_frame_color_left = cv2.bitwise_and(depth_frame_color[:h, :w // 2], depth_frame_color[:h, :w // 2], mask=mask_left)
        depth_frame_color_right = cv2.bitwise_and(depth_frame_color[:h, w // 2:], depth_frame_color[:h, w // 2:], mask=mask_right)

    depth_frame_color = np.hstack((depth_frame_color_left, depth_frame_color_right))
    depth_frame_color = adjust_brightness(depth_frame_color)
    depth_frame_copy = depth_frame_color.copy()

    distances_in_mask_left = []
    distances_in_mask_right = []

    xmin, ymin, xmax, ymax = 0, 0, 0, 0

    for depth_data in slc_data:
        roi = depth_data.config.roi
        roi = roi.denormalize(width=depth_frame_color.shape[1], height=depth_frame_color.shape[0])

        xmin = int(roi.topLeft().x)
        ymin = int(roi.topLeft().y)
        xmax = int(roi.bottomRight().x)
        ymax = int(roi.bottomRight().y)

        center_x = int((xmin + xmax) / 2)

        coords = depth_data.spatialCoordinates
        distance = math.sqrt(coords.x ** 2 + coords.y ** 2 + coords.z ** 2)

        if distance == 0 or mask[ymin:ymax, xmin:xmax].sum() == 0:
            continue

        if center_x <= depth_frame_color.shape[1] // 2:
            distances_in_mask_left.append(distance)
        else:
            distances_in_mask_right.append(distance)
    if (len(distances_in_mask_right) > 0 and len([d for d in distances_in_mask_right if d < CRITICAL_DISTANCE]) >= 5) and \
       (len(distances_in_mask_left) > 0 and len([d for d in distances_in_mask_left if d < CRITICAL_DISTANCE]) >= 5):
        message = "ALERTEEE au centre"
        var = 1
        var2 = 7
        # send_serial_command(9)
    elif len(distances_in_mask_right) > 0 and len([d for d in distances_in_mask_right if d < CRITICAL_DISTANCE]) >= 5:
        message = "ALERTEEE a droite"
        # send_serial_command(7)
        var = 7
    elif len(distances_in_mask_left) > 0 and len([d for d in distances_in_mask_left if d < CRITICAL_DISTANCE]) >= 5:
        message = "ALERTEEE a gauche"
        # send_serial_command(9)
        var = 1

    # Process alerts only when the counter reaches 5
    alert_counter += 1
    if alert_counter >= 6:
        print(CRITICAL_DISTANCE,"cm")
        print(message)
        number += 1
        if message == "ALERTEEE au centre":
            number_center += 1
        elif message == "ALERTEEE a droite":
            number_right += 1
        elif message == "ALERTEEE a gauche":
            number_left +=                send_serial_command(var)
        send_serial_command(var2)
        print(var,var2)
        print('number:',number, 'nombre_left:',number_left, 'nombre_right:',number_right, 'nombre_center:',number_center)

        var = 0
        var2 = 0
        # Reset the counter
        alert_counter = 0
    # print(message)
    # send_serial_command(var)
    # send_serial_command(var2)
    # var = 0
    # var2 = 0
    #  # Reset the counter
    # alert_counter = 0
    # print("VIBRATION")


    # Increment the counter on each callback
   # send_serial_command(0)  # Désactiver tous les vibreurs

    resized_frame = cv2.resize(depth_frame_copy, (0, 0), fx=0.5, fy=0.5)
    cv2.imshow('0_depth', resized_frame)
if __name__ == "__main__":
    main()
