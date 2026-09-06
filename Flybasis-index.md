<script>
  document.addEventListener("DOMContentLoaded", () => {
    const link = document.querySelector("a");
    // remove the link from the DOM
    link.remove();
  });
</script>

<div style="display: flex; align-items: center; justify-content: space-between; margin: 20px 0;">
  <img src="images/black-large.webp" alt="Flybasis Logo" style="width: 100px; margin-right: 20px;">

  <div style="display: flex; gap: 10px;">
    <a href="https://flybasis.com">Flybasis</a>
    <a href="/searchapi.docs">WebSocket API</a>
    <a href="/searchapi.docs/checkout-api">Checkout API</a>
  </div>
</div>

# Flybasis WebSocket API Documentation

Welcome to the Flybasis WebSocket API documentation. This API provides real-time communication capabilities for flight search and booking services using WebSocket technology. It is designed to offer seamless and efficient data exchange, ensuring a responsive and interactive user experience. Below, you'll find detailed information on how to connect, authenticate, and interact with the various endpoints available in our API.

## Table of Contents

- [Host and Route](#host-and-route)
- [Events](#events)
- [Authentication](#authentication)
- [Input Parameters](#input-parameters)
  - [Examples](#examples)
    - [1- One-way search](#1--one-way-search)
    - [2- Round-trip search](#2--round-trip-search)
- [Output type](#output-type)
  - [`flights` event](#flights-event)
- [Getting started with Python](#getting-started-with-python)

## Host and Route

`https://enterprise-api.flybasis.com/sockets/v1/stream-flights`

## Events

| Event Name | Emitted By | In-response to | Description                                                               |
| ---------- | :--------: | :------------: | ------------------------------------------------------------------------- |
| `search`   |   Client   |       -        | When a new search is requested using the [Parameters](#input-parameters). |
| `data`     |   Server   |    `search`    | When a new flight is found that matches the search parameters.            |
| `error`    |   Server   |       -        | When an error occurs on the server when fetching data.                    |

## Authentication

To ensure secure access to the Flybasis WebSocket API, all clients must authenticate before establishing a connection. Authentication is handled via a middleware that verifies the client's credentials.
Once an authentication token is obtained, it should be as used as `auth` when creating a Websocket Client.

An example with Python's socketio client:

```python
import socketio

# Initialize the Socket.IO client
sio = socketio.Client()

...

# Attempting to connect to the WebSocket server
sio.connect('API_ENDPOINT',
            auth={'token': "YOUR_AUTH_TOKEN"},
            retry=True,
            socketio_path='/sockets/v1/stream-flights')
```

## Input Parameters

The API can be operated in two modes:

1. One-way search
2. Round-trip search

The explanation and valid values for each parameter are provided below.

| Parameter Name                                | Type   | Description                                                                           | Valid Values                                                                                                                       |
| --------------------------------------------- | ------ | ------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `tripType`                                    | string | The type of trip the user wants to search for. Can be either "oneway" or "roundtrip". | `oneway`, `roundtrip`                                                                                                              |
| `cabin`                                       | string | The cabin class the user wants to search for.                                         | `Economy`, `Premium Economy`, `Business`, `First`                                                                                  |
| `programs`                                    | array  | The airline programs the user wants to search for.                                    | `AM`, `AC`, `KL`, `AS`, `AA`, `AV`, `BA`, `CM`, `DL`, `EK`, `EY`, `IB`, `B6`, `QF`, `SK`, `SQ`, `NK`, `TP`, `TK`, `UA`, `VS`, `VA` |
| `origin`                                      | array  | The origin city the user wants to search for. Max 3                                   | A list of (IATA codes for) origin cities.                                                                                          |
| `destination`                                 | array  | The destination city the user wants to search for. Max 3                              | A list of (IATA codes for) destination cities.                                                                                     |
| `departureDate`                               | object | The departure date the user wants to search for.                                      | `DateObject`, details below                                                                                                        |
| `returnDate` (required only for `roundtrip`)  | object | The arrival date the user wants to search for.                                        | `DateObject`, details below                                                                                                        |

**Date Object**

| Parameter Name | Type   | Description                                      | Valid Values                                                                                                                                                                                                                                                                  |
| -------------- | ------ | ------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `value`        | string | The date the user wants to search for.           | A date string in the format `YYYY-MM-DD`.                                                                                                                                                                                                                                     |
| `range`        | number | The range of dates the user wants to search for. | A number representing the number of days before and after the `value` date. For example, for value =`2024-04-12` and range = 3, the search will be for flights departing on `2024-04-09`, `2024-04-10`, `2024-04-11`, `2024-04-12`, `2024-04-13`, `2024-04-14`, `2024-04-15`. |

### Examples

#### 1- One-way search

```typescript
// One way search
{
  "tripType": "oneway",
  "origin": ['JFK'],
  "destination": ["LHR"],
  "departureDate": {"value": "2025-10-01", "range": 0},
  "pax": "1",
  "cabin": "Business",
  "programs": ['KL', 'DL', 'EK', 'QF', 'TK', 'AC', 'AA'],
}
```

#### 2- Round-trip search

```typescript
// Two way search
{
  "tripType": "roundtrip",
  "origin": ['ORD'],
  "destination": ["LHR"],
  "departureDate": {"value": "2025-10-01", "range": 0},
  "returnDate": {"value": "2025-11-04", "range": 0},
  "pax": "1",
  "cabin": "Business",
  "programs": ['KL', 'DL', 'EK', 'QF', 'TK', 'AC', 'AA'],
}
```

## Output type

### `data` event

Sample data from the event can be previewed [here](https://flybasis.github.io/searchapi.docs/static/response.json). It returns an array of arrays of flights, if round trip, it returns two arrays of flights and one array of flights if one way.

**Sample Flight Object (Single)**

**`Flight` Object**

```typescript
type Flight = {
  id: string;
  legs: {
    origin: string; // iata of the origin airport
    destination: string; // iata of the destination airport
    departure: string; // departure time in the format YYYY-MM-DDTHH:MM:SS
    arrival: string; // arrival time in the format YYYY-MM-DDTHH:MM:SS
    airline: string; // iata of the airline
    flightNumber: string; // flight number without the airline code prefix
    cabin: Cabins; // cabin class
    duration: number; // in minutes
    aircraft: string; // name of the aircraft
    distance: number; // in miles
    layover: number; // in minutes, 0 if there is no leg after the current one
  }[];
  surcharge: number; // amount of surcharge in USD
  points: number;
  program: string; // iata code of the frequent flyer program
  basis: {             // bookability information about the flight
    bookable: boolean; // indicates whether this flight is bookable via Flybasis
    cpm: number;       // the cpm @ which the flight is bookable via Flybasis
  }
};

export enum Cabins {
  Economy = "e",
  "Premium Economy" = "p",
  Business = "b",
  First = "f",
}
```
The id on the flight is not guaranteed to be unique, it is possible to have two flights with the same id.

## Getting started with Python

The following code serves as a POC on how an end-user is expected to use our API. This is subject to modifications in the future.

```python
import socketio
import json

sio = socketio.Client()

ret_flights = [[], []]


@sio.event
def connect():
    print("Connected to server")


@sio.event
def disconnect(resp):
    print("Disconnected from server")


@sio.event
def data(resp, _):
    data = resp["data"]
    flights = data["awd"]
    ret_flights[0].extend(flights[0])
    ret_flights[1].extend(flights[1])


@sio.event
def error(resp, _):
    print("Error", resp)


def main():
    try:
        # Connect to the Socket.IO server
        sio.connect(
            url="API_ENDPOINT",
            auth={"token": "YOUR_AUTH_TOKEN"},
            retry=True,
            socketio_path="/sockets/v1/stream-flights",
            transports=["websocket"],
        )

        # Send a test message to the server
        sq = {
            "tripType": "roundtrip",
            "origin": ['ORD'],
            "destination": ["LHR"],
            "departureDate": {"value": "2025-10-01", "range": 0},
            "returnDate": {"value": "2025-11-04", "range": 0},
            "pax": "1",
            "cabin": "Business",
            "programs": ['KL', 'DL', 'EK', 'QF', 'TK', 'AC', 'AA'],
        }
        sio.emit("search", sq)

        sio.wait()

    except Exception as e:
        print(f"An error occurred: {e}")

    finally:
        if sio.connected:
            sio.disconnect()
        # write to json
        with open("flights.json", "w") as f:
            json.dump(ret_flights, f)


if __name__ == "__main__":
    main()
```
