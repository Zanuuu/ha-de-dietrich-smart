# De Dietrich Smart for Home Assistant

Custom integration for De Dietrich gas boilers with a **SMART TC°** thermostat (developed on an
EMC-S 24/28 MI with floor heating), through the BDR Thermea cloud used by the De Dietrich and
Remeha Home apps.

## Features

- **Climate** entity for the heating zone: setpoint, schedule / manual / frost protection /
  fireplace presets, heating demand. A setpoint change lasts until the next switch by default
  (1 h, 2 h or permanent in the options).
- **Water heater** entity: schedule, comfort or eco mode, comfort setpoint, boost button.
- **Sensors**: room and outdoor temperature, water pressure, boiler activity, error status and
  fault code (with the meaning and advice from the EMC-S manual), next switch, override end,
  heating ratio over the last hour, flow temperature from the heating curve, current setpoint
  (kept as hourly statistics for the curve diagnosis), heating curve recommendation, gas used today.
- **Settings**: heating curve slope and base, setpoints of the five thermostat activities
  (Sleep, Home, Away, Morning, Evening), hot water setpoints, active program, fireplace mode.
- **Energy dashboard**: hourly gas consumption for heating and hot water as external statistics
  (`De Dietrich Smart: Gaz chauffage` and `Gaz eau chaude`), with up to 400 days of history
  imported on first start. The Energy dashboard cannot apply a price to external statistics:
  set the gas price in the integration options, then pick the cost statistics
  (`Coût gaz chauffage`, `Coût gaz eau chaude`) with "Use an entity tracking the total costs".
  The whole history is computed at the current price.
- **Sidebar panel** (French): weekly programs editable day by day, with copy to other days, a
  **heating curve** tab, and a journal of events and commands. Any command can be undone within
  2 minutes.
- **Heating curve**: chart of the current curve, the proposed one and the zone forbidden to floor
  heating, adjustment with confirmation, and a **diagnosis** over the last 14 days of hourly
  statistics. Hours are grouped by outdoor temperature (mild > 8 °C, cool 0 to 8 °C, cold < 0 °C),
  and the gap between room temperature and setpoint is measured while in comfort setpoint. The
  diagnosis proposes one small step at a time (cold deficit → slope +0.1, mild deficit → base +2,
  cool deficit → base +1, excess or short cycles → lower) with a confidence level, or a one-time
  switch to a floor heating curve that keeps today's flow temperature around 5 °C outside. The
  curve model is an approximation: the manual only gives a chart, and the room sensor influence is
  unknown.
- **Simulator** (heating curve tab): flow temperature requested by the current curve and by the
  proposed one (or the one being adjusted), hour by hour, for the forecast of the weather entity
  set in the options (hourly, else daily), or replayed over the last 7 days of recorded outdoor
  temperature. It does not predict room temperatures.
- **Floor heating guard**: with floor heating connected directly, a curve outside the floor
  bounds (slope 0.2 to 1.0, base 15 to 35 °C, flow at the design outdoor temperature at most the
  maximum allowed flow temperature) is refused, from entities, services and the panel alike. A
  curve that is already unsafe can still be moved step by step toward safety. Undo restores the
  previous curve as it was. Set the emitters to radiators, or the connection to mixing valve, to
  lift the guard.
- **Services**: `set_override`, `set_holiday`, `cancel_holiday`, `set_week_program`,
  `undo_last_command`.
- **Repairs**: cloud unreachable, low water pressure, boiler fault, and a heating curve unsafe
  for floor heating connected directly to the boiler.

## Installation (HACS)

1. HACS → three-dot menu → **Custom repositories** → `https://github.com/Zanuuu/ha-de-dietrich-smart`,
   category **Integration**.
2. Install, then restart Home Assistant.
3. *Settings → Devices & services → Add integration → De Dietrich Smart*, and sign in with the
   e-mail and password of the De Dietrich app.

Requires Home Assistant 2026.10 or later.

## Options

| Option | Default | Use |
|---|---|---|
| Duration of a setpoint change | Until the next switch | Setpoint changes from Home Assistant |
| Heat emitters | Floor heating | Floor heating guard, unsafe curve repair, diagnosis |
| Floor heating connection | Direct | Direct: the boiler curve sets the floor water temperature |
| Maximum allowed flow temperature | 45 °C | Floor heating guard, unsafe curve repair |
| Design outdoor temperature | −15 °C | Floor heating guard, unsafe curve repair |
| Weather forecast | none | Heating curve simulator (forecast of the coming days) |

## Limitations

- The cloud API is unofficial (documented by [msvisser/remeha_home](https://github.com/msvisser/remeha_home)).
  It may change without notice.
- **One client only**: gateways are reported to drop when two clients poll the same account.
  Stop any other tool (another integration, a script) using the same De Dietrich account.
- The cloud does not report flow and return temperatures, flame or modulation, and usually not
  the fault code itself: read it on the boiler display.
- Without an outdoor sensor, the outdoor temperature comes from the cloud weather service.
- Hourly consumption arrives late and in whole kWh.
- The curve diagnosis needs at least 3 days of statistics (7 for a high confidence), and a band
  only counts with 24 occupied hours: right after installation, and before the first frost for
  the cold band, it reports "not enough data". It judges on the thermostat only; room sensors
  come with the manifold assistant.

## Migration from the former application

The integration replaces the `ancienne application` application of the projet. See
[docs/bascule.md](docs/bascule.md) (French). Releases: [docs/version.md](docs/version.md) (French).

## License

MIT. The bundled [Lit](https://lit.dev) library is under the BSD 3-Clause License.
