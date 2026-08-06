#include <cadmium/celldevs/asymm/coupled.hpp>
#include <cadmium/core/simulation/root_coordinator.hpp>
#include <chrono>
#include <iostream>
#include <sstream>
#include <string>
#include "include/cell.hpp"
#include "include/state.hpp"
#include "include/logger.hpp"

std::shared_ptr<cadmium::celldevs::AsymmCell<State, double>> addCell(
    const std::string& cellId, const std::shared_ptr<const cadmium::celldevs::AsymmCellConfig<State, double>>& cellConfig)
{
    return std::make_shared<Cell>(cellId, cellConfig);
}

std::chrono::system_clock::time_point parseDateTime(const std::string& start_time_str)
{
    std::tm tm = {};
    std::istringstream ss(start_time_str);
    ss >> std::get_time(&tm, "%Y-%m-%d %H:%M:%S");
    if (ss.fail()) {
        throw std::runtime_error("Failed to parse date/time string");
    }
    tm.tm_isdst = -1;
    time_t datime = std::mktime(&tm);
    return std::chrono::system_clock::from_time_t(datime);
}

int main(int argc, char** argv)
{
    if (argc < 4)
    {
        std::cout << "Usage: flood <scenario.json> <output.csv> \"YYYY-MM-DD HH:MM:SS\" "
                     "[duration_seconds] [log_interval_seconds]" << std::endl;
        return 1;
    }

    std::string start_time_str = argv[3];
    auto start_time = parseDateTime(start_time_str);
    // Default simulated horizon: 24h, you must pass a 4th argument
    // to cover a longer simulation time
    double duration = (argc > 4) ? std::stod(argv[4]) : 86400.0;
    // Optional minimum simulated time between two logged rows for the same cell
    double logInterval = (argc > 5) ? std::stod(argv[5]) : 0.0;

    auto model = std::make_shared<cadmium::celldevs::AsymmCellDEVSCoupled<State, double>>("flood", addCell, argv[1]);
    model->buildModel();

    auto rootCoordinator = cadmium::RootCoordinator(model);
    auto logger = std::make_shared<Logger>(argv[2], start_time, logInterval);

    rootCoordinator.setLogger(logger);
    rootCoordinator.start();
    rootCoordinator.simulate(duration);
    rootCoordinator.stop();
    return 0;
}
