#pragma once

#include <cmath>
#include <iostream>
#include <string>
#include <utility>
#include <vector>
#include <nlohmann/json.hpp>

struct State
{
    // Terrain / land data, fixed for the whole run.
    double elevation;         // ground elevation, m
    double roughness;         // roughness coefficient, from land cover
    double infiltrationRate;  // how fast water soaks into the ground, m/s
    int x;
    int y;

    // A river-source cell has its depth forced by riverLevelOverTime, not
    // computed from the local water balance. This is the flood version of
    // the wildfire model's "ignited region" this is a fixed input, not a result with the plugin you can make it easiky.
    bool isRiverSource;
    std::vector<std::pair<double, double>> riverLevelOverTime; // (time_s, forced depth_m)
    std::vector<std::pair<double, double>> rainfallOverTime;   // (time_s, intensity_mm_per_h)

    // Values that keep changing during the run.
    double waterDepth;      // current water depth, in m
    double maxWaterDepth;   // highest depth reached so far,in m
    bool isFlooded;         // true once waterDepth crosses FLOODED_DEPTH_THRESHOLD, then stays true forever
    double lastUpdateTime;  // simulation clock at the last waterDepth update
    double sigma;           

    // Values that get set once and never change again.
    double floodArrivalTime;  // simulation time this cell first became flooded
    double arrivalDirection;  // degrees from north, direction water arrived from
    double arrivalRate;       // m/s, depth rise rate from the strongest neighbour at that moment

    // This is a limitation but this is now a flood-warning model, not a full post-flood model: once a
    // cell is flooded, it stays flooded for the rest of the run, same idea
    // as the wildfire model's "ignited" flag, which never goes back to
    // false either. waterDepth itself keeps being computed as normal
    static constexpr double FLOODED_DEPTH_THRESHOLD = 0.15; // m; depth above which a cell counts as flooded

    State()
        : elevation(0.0)
        , roughness(0.035)
        , infiltrationRate(0.0)
        , x(0)
        , y(0)
        , isRiverSource(false)
        , waterDepth(0.0)
        , maxWaterDepth(0.0)
        , isFlooded(false)
        , lastUpdateTime(0.0)
        , sigma(0.0)
        , floodArrivalTime(INFINITY)
        , arrivalDirection(0.0)
        , arrivalRate(0.0)
    {}
};

// Water depth changes all the time (rain, infiltration, inflow/outflow), so
// a cell keeps waking itself up for as long as it has water, a wet
// neighbour, or rain/river still coming. It only really stops once sigma is infinite on both sides.
inline bool operator!=(const State& a, const State& b)
{
    bool bothStopped = std::isinf(a.sigma) && std::isinf(b.sigma);
    return !bothStopped;
}

inline std::ostream& operator<<(std::ostream& os, const State& s)
{
    return os << s.x << ":" << s.y << ":" << s.waterDepth << ":" << s.maxWaterDepth << ":"
               << s.isFlooded << ":" << s.lastUpdateTime << ":" << s.sigma << ":"
               << s.floodArrivalTime << ":" << s.arrivalDirection << ":" << s.arrivalRate;
}

inline void from_json(const nlohmann::json& j, State& s)
{
    s.x = j.at("x");
    s.y = j.at("y");
    s.elevation = j.at("elevation");
    s.roughness = j.value("roughness", 0.035);
    s.infiltrationRate = j.value("infiltrationRate", 0.0);
    s.waterDepth = j.value("waterDepth", 0.0);
    s.maxWaterDepth = s.waterDepth;
    s.isRiverSource = j.value("isRiverSource", false);
    if (j.contains("riverLevelOverTime")) {
        for (const auto& pt : j.at("riverLevelOverTime")) {
            s.riverLevelOverTime.emplace_back(pt.at(0).get<double>(), pt.at(1).get<double>());
        }
    }
    if (j.contains("rainfallOverTime")) {
        for (const auto& pt : j.at("rainfallOverTime")) {
            s.rainfallOverTime.emplace_back(pt.at(0).get<double>(), pt.at(1).get<double>());
        }
    }
}
