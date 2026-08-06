#pragma once

#include <chrono>
#include <fstream>
#include <sstream>
#include <string>
#include <unordered_map>
#include <utility>
#include <cadmium/core/logger/logger.hpp>
#include "state.hpp"

class Logger : public cadmium::Logger
{
public:

    Logger(const std::string& filepath, std::chrono::system_clock::time_point start_time,
           double minLogInterval = 0.0)
        : cadmium::Logger(), startTime(start_time), filepath(filepath), file()
        , minLogInterval(minLogInterval), lastLoggedTime() {}

    void start() override
    {
        file.open(filepath);
        file << "time,x,y,waterDepth,maxWaterDepth,isFlooded,simtime,sigma,"
                "floodArrivalTime,arrivalDirection,arrivalRate" << std::endl;
    }

    void stop() override
    {
        file.close();
    }

    void logOutput(double time, long modelId, const std::string& modelName, const std::string& portName, const std::string& output) override {}

    void logState(double time, long modelId, const std::string& modelName, const std::string& state) override
    {
        std::istringstream stream(state);
        std::string x, y, waterDepth, maxWaterDepth, isFlooded, lastUpdateTime, sigma;
        std::string floodArrivalTime, arrivalDirection, arrivalRate;
        std::getline(stream, x, ':');
        std::getline(stream, y, ':');
        std::getline(stream, waterDepth, ':');
        std::getline(stream, maxWaterDepth, ':');
        std::getline(stream, isFlooded, ':');
        std::getline(stream, lastUpdateTime, ':');
        std::getline(stream, sigma, ':');
        std::getline(stream, floodArrivalTime, ':');
        std::getline(stream, arrivalDirection, ':');
        std::getline(stream, arrivalRate, ':');

        // Only show a cell once it has actually flooded, not the instant a
        // trace of rain lands on it. This matches the wildfire model, which
        // only logs a cell once it ignites, not just because embers are
        // nearby. Rain falls everywhere at once, so gating on "any depth at
        // all" made almost every cell appear together at the start.
        // Gating on maxWaterDepth (a running maximum) means a cell appears
        // exactly when it crosses the flood threshold, and keeps appearing
        // afterwards even if its depth later drops below that threshold.
        if (std::stod(maxWaterDepth) <= State::FLOODED_DEPTH_THRESHOLD)
        {
            return;
        }

        // Optional time-based throttle.
        // Off by default.
        std::string cellKey = x + "_" + y;
        auto it = lastLoggedTime.find(cellKey);
        if (minLogInterval > 0.0 && it != lastLoggedTime.end() && (time - it->second) < minLogInterval)
        {
            return;
        }
        lastLoggedTime[cellKey] = time;

        using namespace std::chrono;
        auto elapsed = duration_cast<system_clock::duration>(duration<double>(time));
        const std::time_t now = system_clock::to_time_t(startTime + elapsed);

        file << std::put_time(std::localtime(&now), "%Y-%m-%d %H:%M:%S");
        file << "," << x;
        file << "," << y;
        file << "," << waterDepth;
        file << "," << maxWaterDepth;
        file << "," << isFlooded;
        file << "," << time;
        file << "," << sigma;
        file << "," << floodArrivalTime;
        file << "," << arrivalDirection;
        file << "," << arrivalRate;
        file << std::endl;
    }

private:
    std::string filepath;
    std::ofstream file;
    std::chrono::system_clock::time_point startTime;
    double minLogInterval;
    std::unordered_map<std::string, double> lastLoggedTime;
};
