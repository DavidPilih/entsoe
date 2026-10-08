const fs = require('fs');
const vm = require('vm');
const assert = require('assert/strict');
const context = {self: {ctx: {}}, console};
vm.createContext(context);
vm.runInContext(fs.readFileSync(__dirname + '/widget interactive chart prices.js', 'utf8'), context);
const t = 1800000000000, dt = 900000;
// Identical schedule gives identical SOC at night or with full sun.
function forecast(sun, command) {
    return context.calculateSocForecast([[t, 50]], [[t, sun]], [], t + dt, t,
        [[t, command]], [[t, 100]], [[t, 200]], {});
}
assert.equal(forecast(0, 1).at(-1).y, 62.5);
assert.equal(forecast(100, 1).at(-1).y, 62.5);
assert.equal(forecast(100, -1).at(-1).y, 37.5);
const limited = context.calculateSocForecast([[t, 50]], [], [], t + dt, t,
    [[t, 1]], [[t, 100]], [[t, 200]], {maxSoc: [[t, 60]]});
assert.equal(limited.at(-1).y, 60);
assert.equal(context.consumptionForecastRole({name: 'forecast_consumption_lower[kW]'}), 'lower');
assert.equal(context.isHiddenDataKey({name: 'forecast_result[EUR]'}), true);
assert.equal(context.isKwDataKey({name: 'forecast_solar[kW]'}), true);
console.log('Widget SOC, units and forecast matching OK');
