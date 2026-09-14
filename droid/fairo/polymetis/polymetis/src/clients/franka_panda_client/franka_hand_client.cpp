#include "polymetis/clients/franka_hand_client.hpp"

#include "spdlog/spdlog.h"
#include <chrono>
#include <cmath>
#include <string>
#include <thread>
#include <time.h>

#include <grpc/grpc.h>

#include "polymetis.grpc.pb.h"
#include "polymetis/utils.h"

using grpc::ClientContext;

FrankaHandClient::FrankaHandClient(std::shared_ptr<grpc::Channel> channel,
                                   YAML::Node config)
    : stub_(GripperServer::NewStub(channel)) {
  // Connect to gripper
  std::string robot_ip = config["robot_ip"].as<std::string>();
  spdlog::info("Connecting to robot_ip {}", robot_ip);
  gripper_.reset(new franka::Gripper(robot_ip));

  // Initialize gripper
  gripper_->homing();
  is_moving_ = false;

  // Initialize server connection
  franka::GripperState franka_gripper_state = gripper_->readOnce();

  GripperMetadata metadata;
  metadata.set_max_width(franka_gripper_state.max_width);
  metadata.set_hz(GRIPPER_HZ);

  ClientContext context;
  Empty empty;
  stub_->InitRobotClient(&context, metadata, &empty);

  spdlog::info("Connected.", robot_ip);
}

void FrankaHandClient::stateThread(void) {
  // Continuously calls readOnce() in the background so the main control loop
  // is not blocked waiting for the ~100ms firmware UDP broadcast interval.
  while (true) {
    // auto t0 = std::chrono::steady_clock::now();
    franka::GripperState s = gripper_->readOnce();
    // auto t1 = std::chrono::steady_clock::now();
    // double readonce_ms = std::chrono::duration<double, std::milli>(t1 - t0).count();
    // spdlog::warn("[profile] readOnce took {:.1f} ms", readonce_ms);

    std::lock_guard<std::mutex> lk(state_mutex_);
    cached_franka_state_ = s;
  }
}

void FrankaHandClient::getGripperState(void) {
  // auto t0 = std::chrono::steady_clock::now();
  franka::GripperState franka_gripper_state;
  {
    // Read from cache populated by stateThread — does not block.
    std::lock_guard<std::mutex> lk(state_mutex_);
    franka_gripper_state = cached_franka_state_;
  }
  // auto t1 = std::chrono::steady_clock::now();
  // double readonce_ms = std::chrono::duration<double, std::milli>(t1 - t0).count();
  // spdlog::warn("[profile] readOnce took {:.1f} ms (> 5 ms)", readonce_ms);

  gripper_state_.set_width(franka_gripper_state.width);
  gripper_state_.set_is_grasped(franka_gripper_state.is_grasped);
  gripper_state_.set_is_moving(is_moving_);
  gripper_state_.set_prev_command_successful(prev_cmd_successful_);

  // gripper_state.time();  // Use current timestamp instead!
  setTimestampToNow(gripper_state_.mutable_timestamp());
}

void FrankaHandClient::applyGripperCommand(void) {
  is_moving_ = true;

  if (gripper_cmd_.grasp()) {
    double eps_inner = (gripper_cmd_.epsilon_inner() < 0)
                           ? EPSILON_INNER
                           : gripper_cmd_.epsilon_inner();
    double eps_outer = (gripper_cmd_.epsilon_outer() < 0)
                           ? EPSILON_OUTER
                           : gripper_cmd_.epsilon_outer();
    spdlog::info("[grasp->hw] width={:.4f} speed={:.4f} force={:.4f} eps_inner={:.4f} eps_outer={:.4f}",
                 gripper_cmd_.width(), gripper_cmd_.speed(), gripper_cmd_.force(),
                 eps_inner, eps_outer);
    prev_cmd_successful_ =
        gripper_->grasp(gripper_cmd_.width(), gripper_cmd_.speed(),
                        gripper_cmd_.force(), eps_inner, eps_outer);
    spdlog::info("[grasp done] success={}", prev_cmd_successful_);
  } else if (gripper_cmd_.stop()) {
    spdlog::info("[stop hw]");
    prev_cmd_successful_ = gripper_->stop();
    spdlog::info("[stop done] success={}", prev_cmd_successful_);
  } else {
    spdlog::info("[move->hw] width={:.4f} speed={:.4f} stop={}",
                gripper_cmd_.width(), gripper_cmd_.speed(), gripper_cmd_.stop());
    prev_cmd_successful_ =
        gripper_->move(gripper_cmd_.width(), gripper_cmd_.speed());
    spdlog::info("[move done] success={}", prev_cmd_successful_);
  }

  is_moving_ = false;
}

void FrankaHandClient::run(void) {
  int period = 1.0 / GRIPPER_HZ;
  int period_ns = period * 1.0e9;

  int timestamp_ns;

  // Start background thread that continuously calls readOnce() into the cache.
  std::thread state_th(&FrankaHandClient::stateThread, this);
  state_th.detach();

  struct timespec abs_target_time;
  clock_gettime(CLOCK_REALTIME, &abs_target_time);
  // auto loop_t0 = std::chrono::steady_clock::now();
  // int profile_iter = 0;
  while (true) {
    // Run control step
    getGripperState();
    // auto loop_t1 = std::chrono::steady_clock::now();
    // if (++profile_iter % 10 == 0) {
    //   double loop_ms = std::chrono::duration<double, std::milli>(loop_t1 - loop_t0).count();
    //   spdlog::info("[profile] full loop iter (last 10 avg): {:.3f} ms", loop_ms / 10.0);
    //   loop_t0 = loop_t1;
    // }

    grpc::ClientContext context;
    status_ = stub_->ControlUpdate(&context, gripper_state_, &gripper_cmd_);

    if (!is_moving_) {
      // Skip if command not updated
      timestamp_ns = gripper_cmd_.timestamp().nanos();
      if (timestamp_ns != prev_cmd_timestamp_ns_ && timestamp_ns) {
        // spdlog::info("[cmd] width={:.4f} speed={:.4f} force={:.4f} grasp={} ts_ns={}",
        //              gripper_cmd_.width(), gripper_cmd_.speed(), gripper_cmd_.force(),
        //              gripper_cmd_.grasp(), timestamp_ns);
        // applyGripperCommand() in separate thread
        // std::thread th(&FrankaHandClient::applyGripperCommand, this);
        // th.detach();
        prev_cmd_timestamp_ns_ = timestamp_ns;
        bool should_execute;
        if (gripper_cmd_.stop()) {
          // Always execute stop commands
          should_execute = true;
          prev_cmd_width_ = -1.0;  // reset so next grasp/move always runs
        } else {
          // Skip grasp/move if target width is close to previous target width:
          // avoids the ~1s firmware timeout when re-commanding the same position.
          constexpr double kWidthDeadband = 0.002;  // 2mm
          should_execute = (prev_cmd_width_ < -0.9 ||
                            std::abs(gripper_cmd_.width() - prev_cmd_width_) >= kWidthDeadband);
          if (should_execute) {
            prev_cmd_width_ = gripper_cmd_.width();
          }
        }

        if (should_execute) {
          std::thread th(&FrankaHandClient::applyGripperCommand, this);
          th.detach();
        }
      }
    }

    // Spin once
    abs_target_time.tv_nsec += period_ns;
    clock_nanosleep(CLOCK_REALTIME, TIMER_ABSTIME, &abs_target_time, nullptr);
  }
}

int main(int argc, char *argv[]) {
  if (argc != 2) {
    spdlog::error("Usage: franka_hand_client /path/to/cfg.yaml");
    return 1;
  }
  YAML::Node config = YAML::LoadFile(argv[1]);

  // Launch client
  std::string control_address = config["control_ip"].as<std::string>() + ":" +
                                config["control_port"].as<std::string>();
  FrankaHandClient franka_hand_client(
      grpc::CreateChannel(control_address, grpc::InsecureChannelCredentials()),
      config);
  franka_hand_client.run();

  // Termination
  spdlog::info("Wait for shutdown; press CTRL+C to close.");

  return 0;
}
