import os
import requests


TB_URL = "https://app.iqfleks.si"


def login(username, password):
    resp = requests.post(
        f"{TB_URL}/api/auth/login",
        json={
            "username": username,
            "password": password
        }
    )

    resp.raise_for_status()

    return resp.json()["token"]


def send_asset_telemetry(asset_id, data, headers):
    url = f"{TB_URL}/api/plugins/telemetry/ASSET/{asset_id}/timeseries/ANY"

    resp = requests.post(
        url,
        json=data,
        headers=headers
    )

    resp.raise_for_status()

    print("Poslano na ThingsBoard:", resp.status_code)


def send_device_telemetry(device_id, data, headers):
    url = f"{TB_URL}/api/plugins/telemetry/DEVICE/{device_id}/timeseries/ANY"

    resp = requests.post(
        url,
        json=data,
        headers=headers
    )

    resp.raise_for_status()

    print("Poslano na ThingsBoard:", resp.status_code)


def send_tb_asset(data, id):

    print("Prejeti podatki:")
    print(data)

    username = os.environ.get(
        "TB_USERNAME",
        "davidpilih4242@gmail.com"
    )

    password = os.environ.get(
        "TB_PASSWORD",
        "Hanaturk2005"
    )

    token = login(username, password)

    headers = {
        "X-Authorization": f"Bearer {token}"
    }

    send_asset_telemetry(
        id,
        data,
        headers
    )


def send_tb_device(data, id):

    print("Prejeti podatki:")
    print(data)

    username = os.environ.get(
        "TB_USERNAME",
        "davidpilih4242@gmail.com"
    )

    password = os.environ.get(
        "TB_PASSWORD",
        "Hanaturk2005"
    )

    token = login(username, password)

    headers = {
        "X-Authorization": f"Bearer {token}"
    }

    send_device_telemetry(
        id,
        data,
        headers
    )


def delete_device_telemetry_key(key, id):

    username = os.environ.get(
        "TB_USERNAME",
        "davidpilih4242@gmail.com"
    )

    password = os.environ.get(
        "TB_PASSWORD",
        "Hanaturk2005"
    )

    token = login(username, password)

    headers = {
        "X-Authorization": f"Bearer {token}"
    }

    url = (
        f"{TB_URL}/api/plugins/telemetry/"
        f"DEVICE/{id}/timeseries/delete"
    )

    params = {
        "keys": key,
        "deleteAllDataForKeys": "true",
        "deleteLatest": "true"
    }

    resp = requests.delete(
        url,
        params=params,
        headers=headers
    )

    print("Status:", resp.status_code)
    print("Response:", resp.text)

    resp.raise_for_status()

    print(
        f"Zbrisan telemetry key '{key}' "
        f"za device {id}"
    )


# delete_device_telemetry_key("charging","23a78dc0-8fd7-11f1-b714-4132923502b2")