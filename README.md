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
  heating ratio over the last hour, flow temperature from the heating curve, gas used today.
- **Settings**: heating curve slope and base, setpoints of the five thermostat activities
  (Sleep, Home, Away, Morning, Evening), hot water setpoints, active program, fireplace mode.
- **Energy dashboard**: hourly gas consumption for heating and hot water as external statistics
  (`De Dietrich Smart: Gaz chauffage` and `Gaz eau chaude`), with up to 400 days of history
  imported on first start. Set the gas price in the Energy dashboard.
- **Sidebar panel** (French): weekly programs editable day by day, with copy to other days, and
  a journal of events and commands. Any command can be undone within 2 minutes.
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
| Heat emitters | Floor heating | Heating curve checks |
| Floor heating connection | Direct | Direct: the boiler curve sets the floor water temperature |
| Maximum allowed flow temperature | 45 °C | Unsafe curve repair |
| Design outdoor temperature | −15 °C | Unsafe curve repair |
| Weather forecast | none | Heating curve simulator (next version) |

## Limitations

- The cloud API is unofficial (documented by [msvisser/remeha_home](https://github.com/msvisser/remeha_home)).
  It may change without notice.
- **One client only**: gateways are reported to drop when two clients poll the same account.
  Stop any other tool (another integration, a script) using the same De Dietrich account.
- The cloud does not report flow and return temperatures, flame or modulation, and usually not
  the fault code itself: read it on the boiler display.
- Without an outdoor sensor, the outdoor temperature comes from the cloud weather service.
- Hourly consumption arrives late and in whole kWh.

## Migration from the former application

The integration replaces the `ancienne application` application of the projet. See
[docs/bascule.md](docs/bascule.md) (French).

## License

MIT. The bundled [Lit](https://lit.dev) library is under the BSD 3-Clause License.
