// Flood cell physics: a diffusive-wave approximation of the 2D shallow-water
// equations (same simplification used by LISFLOOD-FP / CADDIES-style flood
// models). Water moves between neighbour cells following Manning's equation,
// driven by the difference in water-surface level (ground elevation +
// depth). This is what makes ponding and backwater work correctly, instead
// of water just draining straight down the slope.

#pragma once

#include <algorithm>
#include <cmath>
#include <limits>
#include <nlohmann/json.hpp>
#include <cadmium/celldevs/asymm/cell.hpp>
#include <cadmium/celldevs/asymm/config.hpp>
#include "state.hpp"

namespace flood {
    constexpr double GRAVITY = 9.81;
    constexpr double STABILITY_SAFETY_FACTOR = 0.6;

    // How often a cell re-checks itself while it still has water moving.
    // A flooded cell keeps computing forever (its depth must keep flowing
    // to neighbours), so this value is the main way to control how many events.

    constexpr double MIN_UPDATE_INTERVAL = 20.0;     // floor on how often a cell re-checks itself
    constexpr double MAX_UPDATE_INTERVAL = 600.0;    // ceiling on that same interval, so dry/idle cells don't tick too often because when i created this model it can causes long simulation time

    // A river-source cell uses this as its fixed tick period, not a floor
    // like MIN_UPDATE_INTERVAL above. Its depth is just a straight-line
    // interpolation between hydrograph points, so ticking faster than this
    // adds cost but no more accuracy (because in the plugin i made a riverflow by hours).
    constexpr double RIVER_RESYNC_INTERVAL = 20.0;
    constexpr double MIN_FLOW_DEPTH = 0.001;// safety (its to not loop forever)
    constexpr double MIN_STEP_DURATION = 1e-6;
    constexpr int MAX_STEPS_PER_EVENT = 2000;        // safety cap per event

    constexpr double EXCHANGE_DAMPING = 0.08;

    // Linear interpolation over a (time, value) series. Holds the first or
    // last value if the query time is outside the series. Used for both the
    // river level series and the rainfall series.
    inline double interpolateSeries(const std::vector<std::pair<double, double>>& series, double time)
    {
        if (series.empty()) return 0.0;
        if (time <= series.front().first) return series.front().second;
        if (time >= series.back().first) return series.back().second;
        for (std::size_t i = 1; i < series.size(); ++i) {
            if (time <= series[i].first) {
                const auto& [t0, v0] = series[i - 1];
                const auto& [t1, v1] = series[i];
                double fraction = (t1 > t0) ? (time - t0) / (t1 - t0) : 0.0;
                return v0 + fraction * (v1 - v0);
            }
        }
        return series.back().second;
    }
}

class Cell : public cadmium::celldevs::AsymmCell<State, double>
{
public:
    Cell(const std::string& id, const std::shared_ptr<const cadmium::celldevs::AsymmCellConfig<State, double>>& config)
        : cadmium::celldevs::AsymmCell<State, double>(id, config) {}

    [[nodiscard]] State localComputation(State state, const std::unordered_map<
        std::string, cadmium::celldevs::NeighborData<State, double>>& neighborhood) const override
    {
        using namespace flood;

        const bool wasFloodedBefore = state.isFlooded;
        const double elapsedTime = std::max(0.0, this->clock - state.lastUpdateTime);

        /*A river-source cell is a forced boundary condition: its depth
        comes from riverLevelOverTime, it does not exchange flow
        It must keep following the hydrograph even after it first floods*/
        if (state.isRiverSource) {
            state.waterDepth = state.riverLevelOverTime.empty() ? 0.0
                : std::max(0.0, interpolateSeries(state.riverLevelOverTime, this->clock));
            state.maxWaterDepth = std::max(state.maxWaterDepth, state.waterDepth);
            state.isFlooded = state.isFlooded || (state.waterDepth > State::FLOODED_DEPTH_THRESHOLD);
            state.lastUpdateTime = this->clock;
            if (!wasFloodedBefore && state.isFlooded) {
                state.floodArrivalTime = this->clock; 
            }

            bool riverStillChanging = !state.riverLevelOverTime.empty()
                && this->clock < state.riverLevelOverTime.back().first;
            state.sigma = riverStillChanging ? RIVER_RESYNC_INTERVAL : std::numeric_limits<double>::infinity();
            return state;
        }

        // A flooded cell is not like a wildfire "ignited" cell: it can't
        // just stop computing, because its waterDepth must keep flowing to
        // neighbours. Only the isFlooded variable is one-way

        // Rainfall input minus infiltration losses.
        {
            double rainRate = interpolateSeries(state.rainfallOverTime, this->clock) / 1000.0 / 3600.0; // mm/h -> m/s
            double rainfallNetRate = rainRate - state.infiltrationRate;
            state.waterDepth = std::max(0.0, state.waterDepth + rainfallNetRate * elapsedTime);
        }

        // Exchange water with neighbours in small stable steps, instead of
        // one big step. The safe step size depends on the current depth,
        // and depth changes as water moves, so we recompute the safe step
        // every time.
        double nextStepSize = MAX_UPDATE_INTERVAL;
        bool hasWetConnection = state.waterDepth > MIN_FLOW_DEPTH;
        double timeLeft = elapsedTime;
        int steps = 0;

        // Remember the strongest inflow seen during this event, in case this
        // cell floods for the first time below. This becomes
        // arrivalDirection/arrivalRate so its for pure information
        double strongestInflowRate = 0.0;
        int strongestInflowFromX = state.x;
        int strongestInflowFromY = state.y;
        bool hasInflow = false;

        while (timeLeft > 1e-9 && steps < MAX_STEPS_PER_EVENT) {
            ++steps;
            double exchangeRate = 0.0;
            double maxStableStep = MAX_UPDATE_INTERVAL;
            bool connectedThisStep = false;

            for (const auto& [neighborId, neighborData] : neighborhood) {
                if (neighborId == this->id) continue; // self entry: keeps this cell scheduling itself, carries no flow

                const State& neighbor = *neighborData.state;
                double distance = neighborData.vicinity; // metres, from the JSON scenario
                if (distance <= 0.0) continue;

                double selfWaterLevel = state.elevation + state.waterDepth;
                double neighborWaterLevel = neighbor.elevation + neighbor.waterDepth;
                double higherGround = std::max(state.elevation, neighbor.elevation);
                // Depth of water actually free to flow: the higher of the two
                // water levels, above the higher of the two ground levels.
                // Zero or negative means the water has not topped the ground
                // between the two cells yet.
                double flowDepth = std::max(selfWaterLevel, neighborWaterLevel) - higherGround;
                if (flowDepth <= MIN_FLOW_DEPTH) continue;

                connectedThisStep = true;
                double levelDifference = selfWaterLevel - neighborWaterLevel; // > 0 => water leaves this cell because of the level difference
                double slope = std::abs(levelDifference) / distance;
                if (slope <= 0.0) continue;

                double averageRoughness = 0.5 * (state.roughness + neighbor.roughness);
                if (averageRoughness <= 0.0) averageRoughness = 0.035;

                // Manning's equation see ( https://fr.wikipedia.org/wiki/Formule_de_Manning-Strickler ) 
                constexpr double MAX_VELOCITY = 15.0; // m/s
                double velocity = std::min(MAX_VELOCITY,
                    (1.0 / averageRoughness) * std::pow(flowDepth, 2.0 / 3.0) * std::sqrt(slope));
                double flowPerWidth = velocity * flowDepth; // m^2/s
                double flowDirection = (levelDifference > 0.0) ? -1.0 : 1.0; // outflow lowers depth, inflow raises it

                // Cell spacing is used as both the flow width and this
                // cell's footprint (matches how the QGIS plugin builds the
                // grid), so dividing by distance turns a m^2/s flow into a
                // m/s depth-change rate.
                double rate = flowPerWidth / distance;
                exchangeRate += flowDirection * rate;

                if (flowDirection > 0.0 && rate > strongestInflowRate) {
                    strongestInflowRate = rate;
                    strongestInflowFromX = neighbor.x;
                    strongestInflowFromY = neighbor.y;
                    hasInflow = true;
                }

                double stepLimit = STABILITY_SAFETY_FACTOR * distance / std::sqrt(GRAVITY * std::max(flowDepth, MIN_FLOW_DEPTH));
                maxStableStep = std::min(maxStableStep, stepLimit);
            }

            if (!connectedThisStep) break; // nothing left to exchange with right now
            hasWetConnection = true;
            nextStepSize = std::min(nextStepSize, maxStableStep);

            double stepDuration = std::min(timeLeft, std::max(maxStableStep, MIN_STEP_DURATION));
            state.waterDepth = std::max(0.0, state.waterDepth + EXCHANGE_DAMPING * exchangeRate * stepDuration);
            timeLeft -= stepDuration;
        }

        state.maxWaterDepth = std::max(state.maxWaterDepth, state.waterDepth);
        state.isFlooded = state.isFlooded || (state.waterDepth > State::FLOODED_DEPTH_THRESHOLD);
        state.lastUpdateTime = this->clock;

        if (!wasFloodedBefore && state.isFlooded) {
            state.floodArrivalTime = this->clock;
            if (hasInflow) {
                double dx = state.x - strongestInflowFromX;
                double dy = state.y - strongestInflowFromY;
                double direction = std::atan2(dx, dy) * 180.0 / M_PI; // direction the water arrived from
                if (direction < 0.0) direction += 360.0;
                state.arrivalDirection = direction;
                state.arrivalRate = strongestInflowRate;
            }
        }

        bool rainStillComing = !state.rainfallOverTime.empty() && this->clock < state.rainfallOverTime.back().first;
        if (!hasWetConnection && !rainStillComing) {
            state.sigma = std::numeric_limits<double>::infinity(); // dry and nothing pending: stop here
        } else {
            state.sigma = std::clamp(nextStepSize, MIN_UPDATE_INTERVAL, MAX_UPDATE_INTERVAL);
        }

        return state;
    }

    // Return sigma as the output delay.
    [[nodiscard]] double outputDelay(const State& state) const override
    {
        return state.sigma;
    }
};
