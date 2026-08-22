// Create a macOS capture device that carries the meeting AND the microphone.
//
// # Why this exists
//
// `recorder.py`'s macOS path records one avfoundation device. On a stock Mac the only audio
// device is the built-in microphone, so a "meeting recording" contains your half of the
// conversation and nobody else's -- which is the failure this fixes, diagnosed 16 Aug 2026 on a
// machine whose only device was `[0] MacBook Pro Microphone`.
//
// macOS has no system-audio input of its own. The traditional answer is a third-party kernel-side
// HAL driver (BlackHole, Loopback, Soundflower) plus a hand-built aggregate device in Audio MIDI
// Setup. That needs an admin password, and on this machine Homebrew is broken (`/opt/homebrew` is
// owned by another account), so the usual `brew install blackhole-2ch` cannot even run.
//
// macOS 14.2 added Core Audio *process taps*, which capture what the system is playing with no
// driver at all and no admin rights. This builds one, wraps it in an aggregate device alongside
// the default microphone, and leaves that device on the system under a stable name. From there
// nothing else has to change: ffmpeg records it like any other input, exactly as
// `config.json`'s macOS note already anticipated for the BlackHole route.
//
// # What you get
//
// One aggregate device, `ASB Meeting Capture`, whose streams are:
//
//   * a global tap of everything the machine is playing (the other people in the call)
//   * the default input device (you)
//
// # Usage
//
//   asb-systemaudio create    # make it (idempotent) and print its name + UID
//   asb-systemaudio status    # is it there, and does anything still reference it
//   asb-systemaudio remove    # tear it down
//
// Exit code is 0 on success and 1 on failure, with the reason on stderr, so `recorder.py` and the
// app can both branch on it without parsing prose.

import AVFoundation
import CoreAudio
import Foundation

/// Name the device is published under. Deliberately stable and deliberately ours: `recorder.py`
/// finds this device by name rather than by index, because indices renumber whenever a headset is
/// plugged in and an index written into `config.json` silently starts pointing at the wrong thing.
let kDeviceName = "ASB Meeting Capture"

/// UID we assign, so `status` and `remove` can find our own device and never touch a user's.
let kDeviceUID = "com.aisecondbrain.meetingcapture"

func fail(_ msg: String) -> Never {
    FileHandle.standardError.write(Data((msg + "\n").utf8))
    exit(1)
}

/// Read a CoreAudio property that returns a single plain-old-data value.
///
/// Constrained to `BitwiseCopyable` rather than left generic over any `T`: taking a raw pointer to
/// a type that might hold an object reference is what the compiler warns about, and CoreAudio's
/// scalar properties (device ids, counts, flags) are all POD anyway. Properties that return a
/// `CFString` are read separately, where the retain semantics are handled explicitly.
func getProperty<T: BitwiseCopyable>(
    _ object: AudioObjectID,
    _ selector: AudioObjectPropertySelector,
    _ initial: T
) -> T? {
    var address = AudioObjectPropertyAddress(
        mSelector: selector,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var value = initial
    var size = UInt32(MemoryLayout<T>.size)
    let status = withUnsafeMutablePointer(to: &value) {
        AudioObjectGetPropertyData(object, &address, 0, nil, &size, $0)
    }
    return status == noErr ? value : nil
}

/// A device's CoreAudio UID string, or nil when it has none.
func deviceUID(_ device: AudioObjectID) -> String? {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioDevicePropertyDeviceUID,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var uid: CFString? = nil
    var size = UInt32(MemoryLayout<CFString?>.size)
    let status = withUnsafeMutablePointer(to: &uid) {
        AudioObjectGetPropertyData(device, &address, 0, nil, &size, $0)
    }
    guard status == noErr else { return nil }
    return uid as String?
}

/// The system's current default input, as a CoreAudio UID string.
///
/// Resolved at creation time rather than hardcoded: whichever microphone the user has chosen is
/// the one that should end up in the aggregate, and on a laptop that changes every time a headset
/// is connected.
func defaultInputUID() -> String? {
    guard let deviceID = getProperty(
        AudioObjectID(kAudioObjectSystemObject),
        kAudioHardwarePropertyDefaultInputDevice,
        AudioObjectID(0)
    ), deviceID != 0 else { return nil }
    return deviceUID(deviceID)
}

/// Our aggregate device, if it already exists.
///
/// Matched on UID, not on name: a user is free to rename a device in Audio MIDI Setup, and
/// matching on the name would then either lose track of ours or, worse, delete theirs.
func findExistingDevice() -> AudioObjectID? {
    var address = AudioObjectPropertyAddress(
        mSelector: kAudioHardwarePropertyDevices,
        mScope: kAudioObjectPropertyScopeGlobal,
        mElement: kAudioObjectPropertyElementMain
    )
    var dataSize: UInt32 = 0
    guard AudioObjectGetPropertyDataSize(
        AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &dataSize
    ) == noErr else { return nil }

    let count = Int(dataSize) / MemoryLayout<AudioObjectID>.size
    var devices = [AudioObjectID](repeating: 0, count: count)
    guard AudioObjectGetPropertyData(
        AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &dataSize, &devices
    ) == noErr else { return nil }

    for device in devices where deviceUID(device) == kDeviceUID {
        return device
    }
    return nil
}

func createDevice() {
    if let existing = findExistingDevice() {
        print("\(kDeviceName)\t\(kDeviceUID)\talready present (id \(existing))")
        return
    }

    // A global tap: everything the machine plays, minus nothing. `mono` would halve the data for
    // no benefit here -- whisper and the Gemini transcriber both downmix anyway -- but stereo is
    // what the tap natively produces and resampling is ffmpeg's job, not ours.
    let tapDescription = CATapDescription(stereoGlobalTapButExcludeProcesses: [])
    tapDescription.uuid = UUID()
    tapDescription.name = "\(kDeviceName) Tap"
    // Un-muted: the point is to hear the call. A muted tap records the silence this whole file
    // exists to prevent.
    tapDescription.muteBehavior = .unmuted
    tapDescription.isPrivate = false

    var tapID = AudioObjectID(kAudioObjectUnknown)
    let tapStatus = AudioHardwareCreateProcessTap(tapDescription, &tapID)
    guard tapStatus == noErr, tapID != kAudioObjectUnknown else {
        fail("""
             Could not create the system-audio tap (CoreAudio error \(tapStatus)).
             macOS 14.2 or newer is required, and the app asking for it needs audio-capture \
             permission. Check System Settings > Privacy & Security > Microphone.
             """)
    }

    var subDevices: [[String: Any]] = []
    if let micUID = defaultInputUID() {
        subDevices.append([kAudioSubDeviceUIDKey as String: micUID])
    } else {
        // Not fatal. A recording of the call without your voice still captures the meeting, which
        // is strictly better than the microphone-only capture this replaces.
        FileHandle.standardError.write(Data(
            "warning: no default input device found, so the capture will carry system audio only\n".utf8
        ))
    }

    let description: [String: Any] = [
        kAudioAggregateDeviceNameKey as String: kDeviceName,
        kAudioAggregateDeviceUIDKey as String: kDeviceUID,
        // Public and persistent: ffmpeg runs as a separate process and has to be able to see it,
        // and it must survive a reboot so the recorder does not need setup before every meeting.
        kAudioAggregateDeviceIsPrivateKey as String: false,
        kAudioAggregateDeviceIsStackedKey as String: false,
        kAudioAggregateDeviceSubDeviceListKey as String: subDevices,
        kAudioAggregateDeviceTapListKey as String: [
            [
                kAudioSubTapUIDKey as String: tapDescription.uuid.uuidString,
                kAudioSubTapDriftCompensationKey as String: true,
            ]
        ],
    ]

    var aggregateID = AudioObjectID(kAudioObjectUnknown)
    let status = AudioHardwareCreateAggregateDevice(description as CFDictionary, &aggregateID)
    guard status == noErr, aggregateID != kAudioObjectUnknown else {
        AudioHardwareDestroyProcessTap(tapID)
        fail("Could not create the aggregate device (CoreAudio error \(status)).")
    }

    print("\(kDeviceName)\t\(kDeviceUID)\tcreated (id \(aggregateID))")
}

func removeDevice() {
    guard let device = findExistingDevice() else {
        print("nothing to remove")
        return
    }
    let status = AudioHardwareDestroyAggregateDevice(device)
    guard status == noErr else {
        fail("Could not remove the aggregate device (CoreAudio error \(status)).")
    }
    print("removed")
}

func status() {
    if let device = findExistingDevice() {
        print("present\t\(kDeviceName)\t\(kDeviceUID)\tid \(device)")
    } else {
        print("absent")
        exit(1)
    }
}

let command = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "status"
switch command {
case "create": createDevice()
case "remove": removeDevice()
case "status": status()
default:
    fail("usage: asb-systemaudio [create|status|remove]")
}
