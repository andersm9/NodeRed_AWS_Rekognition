import configparser
from datetime import datetime
from datetime import timedelta
from subprocess import Popen
import sys
import time
import requests
import boto3
import json
import paho.mqtt.client as mqtt

def get_meraki_snapshots(session, api_key, net_id, time=None, filters=None):
    # Get devices of network
    headers = {
        'X-Cisco-Meraki-API-Key': api_key,
        # 'Content-Type': 'application/json'  # issue where this is only needed if timestamp specified
    }
    response = session.get(f'https://api.meraki.com/api/v0/networks/{net_id}/devices',headers=headers)
    devices = response.json()
    #filter for MV cameras:
    cameras = [device for device in devices if device['model'][:2] == 'MV']
    # Assemble return data
    for camera in cameras:
        #filter for serial number provided
        if (camera["serial"] == mv_serial):
            # Get snapshot link
            if time:
                headers['Content-Type'] = 'application/json'
                response = session.post(
                    f'https://api.meraki.com/api/v0/networks/{net_id}/cameras/{camera["serial"]}/snapshot',
                    headers=headers,
                    data=json.dumps({'timestamp': time}))
            else:
                response = session.post(
                    f'https://api.meraki.com/api/v0/networks/{net_id}/cameras/{camera["serial"]}/snapshot',
                    headers=headers)

            # Possibly no snapshot if camera offline, photo not retrievable, etc.
            if response.ok:
                snapshot_url = response.json()['url']

    return snapshot_url

# Gather credentials
def gather_credentials():
    cp = configparser.ConfigParser()
    try:
        cp.read('credentials.ini')
        cam_key = cp.get('meraki', 'key2')
        net_id = cp.get('meraki', 'network')
        mv_serial = cp.get('sense', 'serial')
    except:
        print('Missing credentials or input file!')
        sys.exit(2)
    return cam_key, net_id,mv_serial

def send_snap_to_AWS(image):
    print("sending snapshot_url to AWS rekognition")
    session = boto3.Session(profile_name='default')
    rek = session.client('rekognition')
    resp = requests.get(image)
    print(resp)
    rekresp = {}
    resp_txt = str(resp)
    imgbytes = resp.content
    try:
        rekresp = rek.detect_faces(Image={'Bytes': imgbytes}, Attributes=['ALL'])
    except:
        pass
    return(rekresp, resp_txt)

#get labels (e.g House, car etc)
def detect_labels(image, max_labels=10, min_confidence=90):
    rekognition = boto3.client("rekognition")
    resp = requests.get(image)
    imgbytes = resp.content
    label_response = rekognition.detect_labels(
        Image={'Bytes': imgbytes},
        MaxLabels=max_labels,
        MinConfidence=min_confidence,
    )
    #print(str(label_response))
    return label_response['Labels']


# The callback for when the client receives a CONNACK response from the server
def on_connect(client, userdata, flags, rc):
    print(f'Connected with result code {rc}')
    serial = userdata['mv_serial']
    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.
    client.subscribe(f'/merakimv/{serial}/0')
    #client.subscribe(f'/merakimv/{serial}/light')

# The callback for when a PUBLISH message is received from the server
def on_message(client, userdata, msg):
    #triggers image analysis when incoming MQTT data is detected
    analyze()

def analyze():
    #periodially takes snapshot from the Meraki Dashboard and sends to AWS rekognition for analysis
    if True:
        print("Request Snapshot URL")
        #get the URL of a snapshot from our camera
        snapshot_url = get_meraki_snapshots(session, api_key, net_id, None, None)
        #assume the snapshot is not yet available for download:
        resp_txt = "404"
        while ("404" in resp_txt) == True:
            #attempt to access snapshot URL
            rekresp, resp_txt = send_snap_to_AWS(snapshot_url)
            #print(resp_txt)
        #onece the URL is available, send to AWS Rekognition and print results to stdout
        for faceDetail in rekresp['FaceDetails']:
            print('The detected face is between ' + str(faceDetail['AgeRange']['Low']) + ' and ' + str(faceDetail['AgeRange']['High']) + ' years old')
            Age = (((faceDetail['AgeRange']['Low'])+(faceDetail['AgeRange']['High']))/2)
            #Print Emotion/Gender/Age to stdout
            EmotionalState = max(faceDetail['Emotions'], key=lambda x:x['Confidence'])
            Emotion = EmotionalState['Type']
            gender = (faceDetail['Gender']['Value'])
            print(gender)
            print(Emotion)
            print(Age)
            #Publish Emotion/Gender/Age via MQTT to NodeRed
            Publish = {Age:EmotionalState['Type']}
            client.publish("Age",Age)
            client.publish("EmotionalState",Emotion)
            client.publish("Gender",gender)

        #Print Object Detection via MQTT to NodeRed
        labels=[]
        n=0
        Objects_Detected = detect_labels(snapshot_url)
        print(type(Objects_Detected))
        Quantity_Objects = len(Objects_Detected)
        Null = 6 - Quantity_Objects
        print(Quantity_Objects)
        print(Null)
        print("Objects_Detected recieved")
        for label in Objects_Detected:
            entry = str("{Name} - {Confidence}%".format(**label))
            labels.append(entry)
            label = ("Label" + str(n))
            print(label + " " + entry)
            client.publish(label,entry)
            n = n+1
        client.publish("Snap",snapshot_url)
        while Quantity_Objects < 6:
            entry = " - "
            label = ("Label" + str(Quantity_Objects))
            Quantity_Objects = Quantity_Objects + 1
            client.publish(label,entry)
        print("end of objects detected")
# Main function
if __name__ == '__main__':
    # Get credentials
    (api_key, net_id,mv_serial) = gather_credentials()
    user_data = {
        'api_key': api_key,
        'net_id': net_id,
        'mv_serial': mv_serial
    }
    session = requests.Session()
    # Start MQTT client
    client = mqtt.Client()
    client.user_data_set(user_data)
    client.on_connect = on_connect
    client.on_message = on_message
    client.connect('ec2-54-171-108-150.eu-west-1.compute.amazonaws.com', 1883, 300)
    client.loop_forever()
