import aexpect
import os
import time
import logging
import glob

from virttest import remote
from virttest import virsh

from virttest.libvirt_xml import vm_xml
from virttest.utils_libvirt import libvirt_vmxml
from virttest.utils_libvirt import libvirt_disk
from virttest.utils_test import libvirt

from provider.backingchain import blockcommand_base
from provider.backingchain import check_functions
from provider.virtual_disk import disk_base

from provider.migration import base_steps
from provider.virtual_disk.disk_base import DiskBase

LOG = logging.getLogger('avocado.test.' + __name__)


def run(test, params, env):
    """
    Do blockcommit after vm migration

    :param test: test object
    :param params: Dictionary with the test parameters
    :param env: Dictionary with test environment.
    """
    def _clear_snaps():
        """
        delete the snapshot file created in _do_snaps()
        """
        for name in ['s0', 's1', 's2', 's3']:
            path = os.path.join(test_obj.new_image_path, name)
            if os.path.isfile(path):
                LOG.info(f"1111 remove file {path}")
                os.remove(path)

    def setup_test():
        """
        Setup step
        1) Add a disk to the vm with backingchain defined;
        2) Make sure the vm is in expected state before migrate
        """

        def translate_domblklist_output_into_dict():
            """
            A utility to translate raw output with key=value attribute into one dictionary

            :param output: raw output
            :return one dictionary
            """
            data_dict = {}
            blklist = virsh.domblklist(vm_name).stdout_text.splitlines()
            for line in blklist:
                if line.strip().startswith(('hd', 'vd', 'sd', 'xvd')):
                    data_dict.update({"%s" % line.split()[0].strip(): "%s" % line.split()[1].strip()})
            return data_dict

        def _do_snaps():
            """
            Do snaps for the target disk
            """
            snap_option = '--disk-only --no-metadata'
            snap_extra = f'--diskspec {image_target},snapshot=no'
            snap_num = int(4)
            for i in range(snap_num):
                snap_path = os.path.join(test_obj.new_image_path, f's{i}')
                LOG.info(f"snapshot file is {snap_path}")
                option = "%s %s --diskspec %s,file=%s %s" % ('snap%d' % i, snap_option, t_target, snap_path, snap_extra)
                virsh.snapshot_create_as(vm.name, option, ignore_status=False, debug=True)

        new_disk_name = params.get("new_disk_name", "test.qcow2")
        disk_path = os.path.join(mnt_path_name, new_disk_name)
        test_obj.new_image_path = mnt_path_name
        LOG.info(f"Set up env: create an additional disk: {disk_path} and add it to vm...")
        libvirt_disk.create_disk("file", disk_path, disk_format="qcow2")
        virsh.attach_disk(vm_name, disk_path, 'vdb', "--persistent --subdriver qcow2", debug=True)
        if not vm.is_alive():
            vm.start()
        LOG.info("Set up env: Prepare backingchain for the additional disk...")
        blk_dict = translate_domblklist_output_into_dict()
        LOG.info(f"current disk dictionary is: {blk_dict}")
        if len(blk_dict) != 2:
            test.error("There should be 2 disk for the VM!")
        t_target = next((k for k, v in blk_dict.items() if v.endswith(new_disk_name)), None)
        image_target = next((k for k in blk_dict if k != t_target), None)
        LOG.info(f"Do snapshot only on: {t_target} to create backingchain...")
        _do_snaps()

        if vm_state == "paused":
            virsh.suspend(vm_name)
        ret = virsh.domstate(vm_name).stdout_text
        LOG.info(f"current vm state is {ret} before migration")
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        LOG.debug("Current vmxml before migration is:\n%s", vmxml)
        source_list = DiskBase.get_source_list(vmxml, 'file', t_target)
        LOG.debug(f"333333333333 the source list is {source_list} before migration")
        return t_target, image_target

    def block_commit_test():
        """
        After migration, verify the result and do block commit on target host
        1) check the vm status should keep the same, check the backingchain still exists
        2) do blockcommit
        """
        dest_uri = params.get("virsh_migrate_desturi")

        test.log.info("Verify steps after migration")
        backup_uri, vm.connect_uri = vm.connect_uri, dest_uri
        if vm.is_alive():
            migration_obj.verify_default()
            vm.cleanup_serial_console()
            vm.create_serial_console()
            remote_vm_session = vm.wait_for_serial_login(timeout=360)
            remote_vm_session.close()
        ret = virsh.domstate(vm_name, uri=dest_uri, debug=True).stdout_text.strip()
        LOG.info(f"VM state after migration is {ret}")
        if ret != vm_state:
            test.fail(f"vm state is {ret} after migration, while it expected to be {vm_state}!")
        LOG.info("Check the backingchain info")
        vmxml = vm_xml.VMXML.new_from_dumpxml(vm_name)
        LOG.debug("Current vmxml is:\n%s", vmxml)
        source_list = DiskBase.get_source_list(vmxml, 'file', t_disk)
        LOG.debug(f"333333333333 the source list is {source_list}")
        #if source_list != expected_chain:
        #    self.test.fail('Expect source file to be %s, '
        #   'but got %s' % (expected_chain, source_list))
        #else:
        #  LOG.debug('Get correct backingchin')
        #   return True

        ret1 = virsh.blockcommit(vm.name, t_disk, f"--wait --verbose --base {mnt_path_name}/s1 --pivot",
                                 uri=dest_uri, debug=True, ignore_status=False)
        libvirt.check_result(ret1)
        # virsh_remote = virsh.VirshPersistent(uri=migration_obj.vm.connect_uri)
        virsh_remote = virsh.VirshPersistent(uri=dest_uri)
        vmxml_remote = vm_xml.VMXML.new_from_dumpxml(migration_obj.vm.name, virsh_instance=virsh_remote)
        source_list_after = DiskBase.get_source_list(vmxml_remote, 'file', t_disk)
        LOG.debug(f"555555555555555The source list after commit is {source_list_after}")
        



        # Check dump file
        # cmd = "ls %s" % dump_file_path
        # ret = remote.run_remote_cmd(cmd, params, ignore_status=False)
        # if vm_name not in ret.stdout_text.strip():
        #   test.fail("Not found dump file.")

        vm.destroy()
        vm.connect_uri = backup_uri

    def verify_test_back():
        """
        Verify step
        check the vm status should keep the same
        """
        #

    vm_name = params.get("migrate_main_vm")
    vm = env.get_vm(vm_name)
    migration_obj = base_steps.MigrationBase(test, vm, params)
    mnt_path_name = params.get("mnt_path_name")
    test_obj = blockcommand_base.BlockCommand(test, vm, params)
    disk_obj = disk_base.DiskBase(test, vm, params)
    vm_state = params.get("vm_state")

    try:
        migration_obj.setup_connection()
        t_disk, i_disk = setup_test()
        migration_obj.run_migration()
        block_commit_test()
        #migration_obj.run_migration_back()
        #verify_test_back()
    finally:
        _clear_snaps()
        migration_obj.cleanup_connection()
