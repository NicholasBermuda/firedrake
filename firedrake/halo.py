from __future__ import absolute_import, print_function, division
from pyop2 import op2
from pyop2 import utils
from mpi4py import MPI

from firedrake.petsc import PETSc
import firedrake.dmplex as dmplex


_MPI_types = {}


def _get_mtype(dat):
    """Get an MPI datatype corresponding to a Dat.

    This builds (if necessary a contiguous derived datatype of the
    correct size)."""
    key = (dat.dtype, dat.cdim)
    try:
        return _MPI_types[key]
    except KeyError:
        try:
            tdict = MPI.__TypeDict__
        except AttributeError:
            tdict = MPI._typedict
        try:
            btype = tdict[dat.dtype.char]
        except KeyError:
            raise RuntimeError("Unknown base type %r", dat.dtype)
        if dat.cdim == 1:
            typ = btype
        else:
            typ = btype.Create_contiguous(dat.cdim)
            typ.Commit()
        _MPI_types[key] = typ
        return typ


class Halo(op2.Halo):
    """Build a Halo for a function space.

    :arg dm:  The DMPlex describing the topology.
    :arg section: The data layout.

    The halo is implemented using a PETSc SF (star forest) object and
    is usable as a PyOP2 :class:`pyop2.Halo`."""

    def __init__(self, dm, section):
        super(Halo, self).__init__()
        # Use a DM to create the halo SFs
        self.dm = PETSc.DMShell().create(dm.comm)
        self.dm.setPointSF(dm.getPointSF())
        self.dm.setDefaultSection(section)

    @utils.cached_property
    def sf(self):
        lsec = self.dm.getDefaultSection()
        gsec = self.dm.getDefaultGlobalSection()
        self.dm.createDefaultSF(lsec, gsec)
        # The full SF is designed for GlobalToLocal or LocalToGlobal
        # where the input and output buffers are different.  So on the
        # local rank, it copies data from input to output.  However,
        # our halo exchanges use the same buffer for input and output
        # (so we don't need to do the local copy).  To facilitate
        # this, prune the SF to remove all the roots that reference
        # the local rank.
        sf = dmplex.prune_sf(self.dm.getDefaultSF())
        sf.setFromOptions()
        if sf.getType() != sf.Type.BASIC:
            raise RuntimeError("Windowed SFs expose bugs in OpenMPI (use -sf_type basic)")
        return sf

    @utils.cached_property
    def comm(self):
        return self.dm.comm.tompi4py()

    @utils.cached_property
    def local_to_global_numbering(self):
        lsec = self.dm.getDefaultSection()
        gsec = self.dm.getDefaultGlobalSection()
        return dmplex.make_global_numbering(lsec, gsec)
        return self._gnn2unn

    def global_to_local_begin(self, dat, insert_mode):
        assert insert_mode is op2.WRITE, "Only WRITE GtoL supported"
        if self.comm.size == 1:
            return
        mtype = _get_mtype(dat)
        dmplex.halo_begin(self.sf, dat, mtype, False)

    def global_to_local_end(self, dat, insert_mode):
        assert insert_mode is op2.WRITE, "Only WRITE GtoL supported"
        if self.comm.size == 1:
            return
        mtype = _get_mtype(dat)
        dmplex.halo_end(self.sf, dat, mtype, False)

    def local_to_global_begin(self, dat, insert_mode):
        assert insert_mode is op2.INC, "Only INC LtoG supported"
        if self.comm.size == 1:
            return
        mtype = _get_mtype(dat)
        dmplex.halo_begin(self.sf, dat, mtype, True)

    def local_to_global_end(self, dat, insert_mode):
        assert insert_mode is op2.INC, "Only INC LtoG supported"
        if self.comm.size == 1:
            return
        mtype = _get_mtype(dat)
        dmplex.halo_end(self.sf, dat, mtype, True)
