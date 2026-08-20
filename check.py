import json
import paho.mqtt.client as mqtt
import threading
import time


topic_req = "controllers/IQFleks/Entsoe/energy_prices/req"
topic_res = "controllers/IQFleks/Entsoe/energy_prices/res"


# unique_id -> celoten request
requests = {}

# ID-ji, za katere smo dobili response
resolved_ids = set()

# Napake
errors = []

lock = threading.Lock()


def on_connect(client, userdata, flags, reason_code, properties=None):

    if reason_code == 0:

        print("Connected to MQTT")

        client.subscribe(topic_req)
        client.subscribe(topic_res)

        print("Poslušam:", topic_req)
        print("Poslušam:", topic_res)

    else:

        print(
            "MQTT connection error:",
            reason_code
        )


def on_message(client, userdata, msg):

    try:

        raw = msg.payload.decode(
            errors="replace"
        )

        # Najprej poskusimo razparsati JSON
        data = json.loads(raw)

        unique_id = data.get("unique_id")

        if unique_id is None:

            print(
                f"[NAPAKA] Sporočilo brez unique_id "
                f"na {msg.topic}: {data}"
            )

            return


        with lock:

            # ==================================================
            # REQUEST
            # ==================================================

            if msg.topic == topic_req:

                # Shranimo CELOTEN request
                requests[unique_id] = data

                print(
                    f"[REQ] ID={unique_id} | "
                    f"skupaj poslano={len(requests)}"
                )


            # ==================================================
            # RESPONSE
            # ==================================================

            elif msg.topic == topic_res:

                resolved_ids.add(unique_id)

                success = data.get("success")


                # ==================================================
                # RESPONSE Z NAPAKO
                # ==================================================

                if success is False:

                    # Poiščemo originalni request
                    original_request = requests.get(unique_id)


                    # Shranimo response + request
                    errors.append({
                        "response": data,
                        "request": original_request
                    })


                    print("\n" + "!" * 70)

                    print(
                        f"[ERROR] ID={unique_id}"
                    )

                    print(
                        f"Error: {data.get('error')}"
                    )

                    print(
                        f"Message: {data.get('message')}"
                    )


                    # ------------------------------------------
                    # IZPIS ORIGINALNEGA REQUESTA
                    # ------------------------------------------

                    if original_request is not None:

                        print(
                            "\nVhodni podatki:"
                        )

                        print(
                            json.dumps(
                                original_request,
                                indent=4,
                                ensure_ascii=False
                            )
                        )

                    else:

                        print(
                            "\n[WARNING] Originalnega "
                            "requesta za ta ID ni v spominu."
                        )


                    print("!" * 70 + "\n")


                # ==================================================
                # SUCCESS
                # ==================================================

                else:

                    print(
                        f"[RES] ID={unique_id} | "
                        f"success={success}"
                    )


                # ==================================================
                # NEZNAN ID
                # ==================================================

                if unique_id not in requests:

                    print(
                        f"[WARNING] Prejet odgovor za ID "
                        f"{unique_id}, ki še ni bil zaznan "
                        f"na req topicu!"
                    )


    except json.JSONDecodeError as e:

        # Če response sploh ni veljaven JSON,
        # tukaj ne moremo neposredno dobiti unique_id.

        print("\n" + "!" * 70)

        print(
            "[JSON NAPAKA]"
        )

        print(
            "Topic:",
            msg.topic
        )

        print(
            "Raw payload:",
            repr(raw)
        )

        print(
            "Napaka:",
            type(e).__name__,
            "-",
            str(e)
        )

        print("!" * 70 + "\n")


    except Exception as e:

        print(
            "[NAPAKA pri obdelavi]:",
            type(e).__name__,
            "-",
            str(e)
        )


def print_status():

    while True:

        time.sleep(10)

        with lock:

            sent_ids = set(
                requests.keys()
            )

            pending = (
                sent_ids
                - resolved_ids
            )

            unknown = (
                resolved_ids
                - sent_ids
            )


            print("\n" + "=" * 70)

            print(
                "STATUS"
            )

            print("=" * 70)


            print(
                f"Poslanih zahtevkov : {len(sent_ids)}"
            )

            print(
                f"Rešenih zahtevkov  : {len(resolved_ids)}"
            )

            print(
                f"Napak              : {len(errors)}"
            )

            print(
                f"Čakajočih           : {len(pending)}"
            )


            # ==================================================
            # ČAKAJOČI
            # ==================================================

            if pending:

                print(
                    "\nČakajoči ID-ji:"
                )

                print(
                    sorted(pending)
                )


            # ==================================================
            # NEZNANI ID-JI
            # ==================================================

            if unknown:

                print(
                    "\nNEZNANI ID-ji:"
                )

                print(
                    sorted(unknown)
                )


            # ==================================================
            # NAPAKE
            # ==================================================

            if errors:

                print(
                    "\n" + "-" * 70
                )

                print(
                    "NAPAKE:"
                )

                print(
                    "-" * 70
                )


                for error in errors:

                    response = error["response"]

                    request = error["request"]

                    unique_id = response.get(
                        "unique_id"
                    )


                    print(
                        f"\nID={unique_id}"
                    )

                    print(
                        f"Error: {response.get('error')}"
                    )

                    print(
                        f"Message: {response.get('message')}"
                    )


                    if request:

                        print(
                            "Vhodni podatki:"
                        )

                        print(
                            json.dumps(
                                request,
                                indent=4,
                                ensure_ascii=False
                            )
                        )

                    else:

                        print(
                            "Vhodnih podatkov "
                            "za ta ID ni v spominu."
                        )


            # ==================================================
            # KONČNI STATUS
            # ==================================================

            if not pending and sent_ids:

                if not errors and not unknown:

                    print(
                        "\n✓ VSI ZAHTEVKI SO "
                        "USPEŠNO REŠENI"
                    )

                elif errors:

                    print(
                        "\n⚠ VSI ZAHTEVKI SO REŠENI, "
                        "AMPAK NEKATERI Z NAPAKO"
                    )


            print(
                "=" * 70
            )


if __name__ == "__main__":

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2
    )


    client.username_pw_set(
        "iqfleks_mqtt",
        "iqfleks_pass"
    )


    client.on_connect = on_connect

    client.on_message = on_message


    try:

        client.connect(
            "10.188.20.3",
            1884,
            60
        )


        status_thread = threading.Thread(
            target=print_status,
            daemon=True
        )

        status_thread.start()


        print(
            "CHECK program zagnan"
        )


        client.loop_forever()


    except KeyboardInterrupt:

        print(
            "Ustavljen"
        )


    except Exception as e:

        print(
            "CHECK program se je ustavil:",
            type(e).__name__,
            "-",
            str(e)
        )


    finally:

        client.disconnect()

        print(
            "CHECK program ustavljen"
        )