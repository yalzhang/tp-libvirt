# pylint: disable=spelling
# disable pylint spell checker to allow for dasda, fdasda, vdb, vda, virtio, blk
import logging as log
import re

from avocado.core.exceptions import TestError

from virttest import virsh
from virttest.utils_zchannels import SubchannelPaths as paths
from virttest.utils_misc import cmd_status_output, wait_for
from virttest.libvirt_xml.vm_xml import VMXML

from provider.vfio import ccw

TEST_DASD_ID = None
TARGET = "vdb"  # suppose guest has only one disk 'vda'
cleanup_actions = []


# Using as lower capital is not the best way to do, but this is just a
# workaround to avoid changing the entire file.
logging = log.getLogger('avocado.' + __name__)


def get_partitioned_dasd_path(devid):
    """
    Selects and prepares DASD for test case

    :param devid: the ccw device id, e.g. 0.0.5200
    :return path: absolute path to block device, e.g. '/dev/dasda'
    """
    device = ccw.get_device_info(devid, True)
    global TEST_DASD_ID
    TEST_DASD_ID = device[paths.HEADER["Device"]]
    try_enable_disk(TEST_DASD_ID)
    disk_path = get_device_path(TEST_DASD_ID)
    wait_for(lambda: ccw.format_dasd(disk_path, None), 10, first=1.0)
    wait_for(lambda: ccw.make_dasd_part(disk_path, None), 10, first=1.0)
    return disk_path


def try_enable_disk(disk_id):
    """
    Enables the disk so it can be used

    :param id: disk id cssid.ssid.devno, e.g. 0.0.5000
    :raises: TestError if can't use disk
    """

    cmd = "chzdev -e %s" % disk_id
    err, out = cmd_status_output(cmd, shell=True)
    if 'already configured' not in out:
        cleanup_actions.append(lambda: disable_disk(disk_id))
        if err:
            raise TestError("Couldn't enable dasd '%s'. %s" % (disk_id, out))


def disable_disk(disk_id):
    """
    Enables the disk so it can be used

    :param disk_id: disk id cssid.ssid.devno, e.g. 0.0.5000
    :raises: TestError if can't use disk
    """

    cmd = "chzdev -d %s" % disk_id
    err, out = cmd_status_output(cmd, shell=True)
    if err:
        raise TestError("Couldn't disable dasd '%s'. %s" % (disk_id, out))


def get_device_path(disk_id):
    """
    Gets the device path for the DASD disk

    :param disk_id: disk id cssid.ssid.devno, e.g. 0.0.5000
    :return: absolute device path, e.g. '/dev/dasda'
    """

    cmd = "lszdev %s" % disk_id
    err, out = cmd_status_output(cmd, shell=True)
    if err:
        raise TestError("Couldn't get device info. %s" % out)
    """ Expected output looks like:
    TYPE       ID        ON   PERS  NAMES
    dasd-eckd  0.0.5000  yes  yes   dasda
    """
    try:
        info = out.split('\n')
        values = re.split(r"\s+", info[1])
        name = values[-1]
        return "/dev/" + name
    except:
        raise TestError("Couldn't create device path from '%s', '%s', '%s'" %
                        (out, info, values))


def attach_disk(vm_name, target, path):
    """
    Attaches the disk on path as block device

    :param vm_name: VM name
    :param target: //target@dev, e.g. 'vdb'
    :param path: device path e.g. '/dev/dasda'
    """

    source_info = " --sourcetype block"
    virsh.attach_disk(vm_name, path, target, source_info, ignore_status=False)


def detach_disk(vm_name, target):
    """
    Detaches the target disk

    :param vm_name: VM name
    :param target: //target@dev, e.g. 'vdb'
    """

    virsh.detach_disk(vm_name, target, ignore_status=False)


def check_dasd_partition_table(session, device_target):
    """
    Checks that the partition table can be read
    with 'fdasd'

    :param session: guest session, run command on host if None
    :param device_target: the expected target device name, e.g. 'vdb'
    """

    cmd = "fdasd -p /dev/%s" % device_target
    err, out = cmd_status_output(cmd, shell=True, session=session)
    if err or not re.findall("reading vtoc.*ok", out):
        raise TestError("Couldn't get partition table. %s" % out)
    logging.debug("Confirmed partition table was read correctly:")
    logging.debug(out)


def run(test, params, env):
    """
    Confirm native 'dasd' partitions can be read
    when attached via 'virtio-blk'
    """

    vm_name = params.get("main_vm")
    vm = env.get_vm(vm_name)

    vmxml = VMXML.new_from_inactive_dumpxml(vm_name)
    backup_xml = vmxml.copy()
    devid = params.get("devid")

    try:
        cleanup_actions.append(lambda: vm.destroy())
        disk_path = get_partitioned_dasd_path(devid)

        attach_disk(vm_name, TARGET, disk_path)

        session = vm.wait_for_login()
        check_dasd_partition_table(session, TARGET)
        detach_disk(vm_name, TARGET)
    finally:
        cleanup_actions.reverse()
        for action in cleanup_actions:
            action()
