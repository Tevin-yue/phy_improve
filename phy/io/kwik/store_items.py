# -*- coding: utf-8 -*-
from __future__ import print_function

"""Store items for Kwik."""


#------------------------------------------------------------------------------
# Imports
#------------------------------------------------------------------------------

import os
import os.path as op

import numpy as np

from ...utils import Selector
from ...utils.array import (_index_of,
                            _spikes_per_cluster,
                            _concatenate_per_cluster_arrays,
                            )
from ..store import ClusterStore, StoreItem


#------------------------------------------------------------------------------
# Store items
#------------------------------------------------------------------------------

def _default_array(shape, value=0, n_spikes=0, dtype=np.float32):
    shape = (n_spikes,) + shape[1:]
    out = np.empty(shape, dtype=dtype)
    out.fill(value)
    return out


def _atleast_nd(arr, ndim):
    if arr.ndim == ndim:
        return arr
    assert arr.ndim < ndim
    if ndim - arr.ndim == 1:
        return arr[None, ...]
    elif ndim - arr.ndim == 2:
        return arr[None, None, ...]


class FeatureMasks(StoreItem):
    """Store all features and masks of all clusters."""
    name = 'features and masks'
    fields = ['features', 'masks']

    def __init__(self, *args, **kwargs):
        # Size of the chunk used when reading features and masks from the HDF5
        # .kwx file.
        self.chunk_size = kwargs.pop('chunk_size')

        super(FeatureMasks, self).__init__(*args, **kwargs)

        self.n_features = self.model.n_features_per_channel
        self.n_channels = len(self.model.channel_order)
        self.n_spikes = self.model.n_spikes
        self.n_chunks = self.n_spikes // self.chunk_size + 1

    def _store(self,
               cluster,
               chunk_spikes,
               chunk_spikes_per_cluster,
               chunk_features_masks,
               ):

        nc = self.n_channels
        nf = self.n_features

        # Number of spikes in the cluster and in the current
        # chunk.
        ns = len(chunk_spikes_per_cluster[cluster])

        # Find the indices of the spikes in that cluster
        # relative to the chunk.
        idx = _index_of(chunk_spikes_per_cluster[cluster], chunk_spikes)

        # Extract features and masks for that cluster, in the
        # current chunk.
        tmp = chunk_features_masks[idx, :]

        # NOTE: channel order has already been taken into account
        # by SpikeDetekt2 when saving the features and wavforms.
        # All we need to know here is the number of channels
        # in channel_order, there is no need to reorder.

        # Features.
        f = tmp[:, :nc * nf, 0]
        assert f.shape == (ns, nc * nf)
        f = f.ravel().astype(np.float32)

        # Masks.
        m = tmp[:, :nc * nf, 1][:, ::nf]
        assert m.shape == (ns, nc)
        m = m.ravel().astype(np.float32)

        # Save the data to disk.
        self.disk_store.store(cluster,
                              features=f,
                              masks=m,
                              append=True,
                              )

    def is_consistent(self, cluster, spikes):
        """Return whether the filesizes of the two cluster store files
        (`.features` and `.masks`) are correct."""
        cluster_size = len(spikes)
        expected_file_sizes = [('masks', (cluster_size *
                                          self.n_channels *
                                          4)),
                               ('features', (cluster_size *
                                             self.n_channels *
                                             self.n_features *
                                             4))]
        for name, expected_file_size in expected_file_sizes:
            path = self.disk_store._cluster_path(cluster, name)
            if not op.exists(path):
                return False
            actual_file_size = os.stat(path).st_size
            if expected_file_size != actual_file_size:
                return False
        return True

    def store_all(self, mode=None):
        """Store the features and masks of the clusters that need it.

        Parameters
        ----------

        mode : str or None
            How to choose whether cluster files need to be re-generated.
            Can be one of the following options:

            * `None` or `default`: only regenerate the missing or inconsistent
              clusters
            * `force`: fully regenerate all clusters
            * `read-only`: just load the existing files, do not write anything

        """

        # No need to regenerate the cluster store if it exists and is valid.
        clusters_to_generate = self.to_generate(mode=mode)
        need_generate = len(clusters_to_generate) > 0

        if need_generate:

            self._pr.value_max = self.n_chunks

            fm = self.model.features_masks
            assert fm.shape[0] == self.n_spikes

            for i in range(self.n_chunks):
                a, b = i * self.chunk_size, (i + 1) * self.chunk_size

                # Load a chunk from HDF5.
                chunk_features_masks = fm[a:b]
                assert isinstance(chunk_features_masks, np.ndarray)
                if chunk_features_masks.shape[0] == 0:
                    break

                chunk_spike_clusters = self.model.spike_clusters[a:b]
                chunk_spikes = np.arange(a, b)

                # Split the spikes.
                chunk_spc = _spikes_per_cluster(chunk_spikes,
                                                chunk_spike_clusters)

                # Go through the clusters appearing in the chunk and that
                # need to be re-generated.
                clusters = (set(chunk_spc.keys()).
                            intersection(set(clusters_to_generate)))
                for cluster in sorted(clusters):
                    self._store(cluster,
                                chunk_spikes,
                                chunk_spc,
                                chunk_features_masks,
                                )

                # Update the progress reporter.
                self._pr.value += 1

        self._pr.set_complete()

    @property
    def masks_shape(self):
        return (-1, self.n_channels)

    @property
    def features_shape(self):
        return (-1, self.n_channels, self.n_features)

    def empty_values(self, name):
        # Default masks and features.
        return _default_array(getattr(self, name + '_shape'),
                              value=0. if name == 'features' else 1.,
                              )

    def load(self, cluster, name):
        """Load features or masks for a cluster.

        This uses the cluster store if possible, otherwise it falls back
        to the model (much slower).

        """
        assert name in ('features', 'masks')
        dtype = np.float32
        shape = (self.features_shape if name == 'features'
                 else self.masks_shape)
        if self.disk_store:
            data = self.disk_store.load(cluster, name, dtype, shape)
            if data is not None:
                return data
        # Fallback to load_spikes if the data could not be obtained from
        # the store.
        spikes = self.spikes_per_cluster[cluster]
        return self.load_spikes(spikes, name)

    def load_spikes(self, spikes, name):
        """Load features or masks for an array of spikes."""
        assert name in ('features', 'masks')
        data = getattr(self.model, name)
        if data is not None:
            out = data[spikes]
            return _atleast_nd(out, 2 if name == 'masks' else 3)
        # Default masks and features.
        return _default_array(getattr(self, name + '_shape'),
                              value=0. if name == 'features' else 1.,
                              n_spikes=len(spikes),
                              )

    def load_multi(self, clusters, name):
        if not len(clusters):
            return self.empty_values(name)
        arrays = {cluster: self.load(cluster, name)
                  for cluster in clusters}
        return self._concat(arrays)

    def on_merge(self, up):
        """Create the cluster store files of the merged cluster
        from the files of the old clusters.

        This is basically a concatenation of arrays, but the spike order
        needs to be taken into account.

        """
        clusters = up.deleted
        spc = up.old_spikes_per_cluster
        # We load all masks and features of the merged clusters.
        for name, shape in [('features',
                             (-1, self.n_channels, self.n_features)),
                            ('masks',
                             (-1, self.n_channels)),
                            ]:
            arrays = {cluster: self.disk_store.load(cluster,
                                                    name,
                                                    dtype=np.float32,
                                                    shape=shape)
                      for cluster in clusters}
            # Then, we concatenate them using the right insertion order
            # as defined by the spikes.

            # OPTIM: this could be made a bit faster by passing
            # both arrays at once.
            concat = _concatenate_per_cluster_arrays(spc, arrays)

            # Finally, we store the result into the new cluster.
            self.disk_store.store(up.added[0], **{name: concat})

    def on_assign(self, up):
        """Create the cluster store files of the new clusters
        from the files of the old clusters.

        The files of all old clusters are loaded, re-split and concatenated
        to form the new cluster files.

        """
        for name, shape in [('features',
                             (-1, self.n_channels, self.n_features)),
                            ('masks',
                             (-1, self.n_channels)),
                            ]:
            # Load all data from the old clusters.
            old_arrays = {cluster: self.disk_store.load(cluster,
                                                        name,
                                                        dtype=np.float32,
                                                        shape=shape)
                          for cluster in up.deleted}
            # Create the new arrays.
            for new in up.added:
                # Find the old clusters which are parents of the current
                # new cluster.
                old_clusters = [o
                                for (o, n) in up.descendants
                                if n == new]
                # Spikes per old cluster, used to create
                # the concatenated array.
                spc = {}
                old_arrays_sub = {}
                # Find the relative spike indices of every old cluster
                # for the current new cluster.
                for old in old_clusters:
                    # Find the spike indices in the old and new cluster.
                    old_spikes = up.old_spikes_per_cluster[old]
                    new_spikes = up.new_spikes_per_cluster[new]
                    old_in_new = np.in1d(old_spikes, new_spikes)
                    old_spikes_subset = old_spikes[old_in_new]
                    spc[old] = old_spikes_subset
                    # Extract the data from the old cluster to
                    # be moved to the new cluster.
                    old_spikes_rel = _index_of(old_spikes_subset,
                                               old_spikes)
                    old_arrays_sub[old] = old_arrays[old][old_spikes_rel]
                # Construct the array of the new cluster.
                concat = _concatenate_per_cluster_arrays(spc,
                                                         old_arrays_sub)
                # Save it in the cluster store.
                self.disk_store.store(new, **{name: concat})


class Waveforms(StoreItem):
    """A cluster store item that manages the waveforms of all clusters."""
    name = 'waveforms'
    fields = ['waveforms']

    def __init__(self, *args, **kwargs):
        self.n_spikes_max = kwargs.pop('n_spikes_max')
        self.excerpt_size = kwargs.pop('excerpt_size')

        super(Waveforms, self).__init__(*args, **kwargs)

        self.n_channels = len(self.model.channel_order)
        self.n_spikes = self.model.n_spikes
        self.n_samples = self.model.n_samples_waveforms

        # Get or create the subset spikes per cluster dictionary.
        spc = self.disk_store.load_file('waveforms_spikes')
        if spc is None:
            spc = self._subset_spikes()
            self.disk_store.save_file('waveforms_spikes', spc)
        self._spikes_per_cluster = spc

    def _subset_spikes(self):
        """Create a new `spikes_per_cluster` array with the spikes subset."""
        self._selector = Selector(self.model.spike_clusters,
                                  n_spikes_max=self.n_spikes_max,
                                  excerpt_size=self.excerpt_size,
                                  )
        # Take a selection of spikes.
        spikes = self._selector.subset_spikes_clusters(self.cluster_ids)
        return _spikes_per_cluster(spikes, self.model.spike_clusters[spikes])

    def store(self, cluster):
        # spikes = self._selector.subset_spikes_clusters([cluster])
        spikes = self.spikes_per_cluster[cluster]
        waveforms = self.model.waveforms[spikes]
        self.disk_store.store(cluster,
                              waveforms=waveforms.astype(np.float32),
                              # waveforms_spikes=spikes.astype(np.int64),
                              )

    def is_consistent(self, cluster, spikes):
        """Return whether the waveforms and spikes match."""
        path_w = self.disk_store._cluster_path(cluster, 'waveforms')
        if not op.exists(path_w):
            return False
        file_size_w = os.stat(path_w).st_size
        n_spikes_w = file_size_w // (self.n_channels * self.n_samples * 4)
        if n_spikes_w != len(self.spikes_per_cluster[cluster]):
            return False
        return True

    @property
    def shape(self):
        return (-1, self.n_samples, self.n_channels)

    def empty_values(self, name):
        # Default waveforms.
        return _default_array(self.shape, value=0.)

    def load(self, cluster, name='waveforms'):
        """Load features or masks for a cluster.

        This uses the cluster store if possible, otherwise it falls back
        to the model (much slower).

        """
        assert name == 'waveforms'
        dtype = np.float32
        if self.disk_store:
            data = self.disk_store.load(cluster, name, dtype, self.shape)
            if data is not None:
                return data
        # Fallback to load_spikes if the data could not be obtained from
        # the store.
        spikes = self.spikes_per_cluster[cluster]
        return self.load_spikes(spikes, name)

    def load_multi(self, clusters, name):
        if not len(clusters):
            return self.empty_values(name)
        arrays = {cluster: self.load(cluster, name)
                  for cluster in clusters}
        return self._concat(arrays)

    def load_spikes(self, spikes, name):
        """Load features or masks for an array of spikes."""
        assert name == 'waveforms'
        data = getattr(self.model, name)
        if data is not None:
            return data[spikes]
        # Default waveforms.
        return _default_array(self.shape, value=0., n_spikes=len(spikes))


class ClusterStatistics(StoreItem):
    """Manage cluster statistics."""
    name = 'statistics'
    fields = ['mean_masks',
              'sum_masks',
              'n_unmasked_channels',
              'main_channels',
              'mean_probe_position',
              'mean_features',
              'mean_waveforms',
              ]

    def __init__(self, *args, **kwargs):
        super(ClusterStatistics, self).__init__(*args, **kwargs)
        self._funcs = {}
        self.n_channels = len(self.model.channel_order)
        self.n_samples_waveforms = self.model.n_samples_waveforms
        self.n_features = self.model.n_features_per_channel

    def add(self, name, func):
        """Add a new statistics."""
        self.fields.append(name)
        self._funcs[name] = func

    def remove(self, name):
        """Remove a statistics."""
        self.fields.remove(name)
        del self.funcs[name]

    def store_default(self, cluster):
        """Compute the built-in statistics for one cluster."""
        masks = self.cluster_store.masks(cluster)
        features = self.cluster_store.features(cluster)
        waveforms = self.cluster_store.waveforms(cluster)

        def _mean(arr, shape):
            if arr is not None:
                assert isinstance(arr, np.ndarray)
                if arr.shape[0]:
                    return arr.mean(axis=0)
            return np.zeros(shape, dtype=np.float32)

        # Default statistics.
        mean_masks = _mean(masks, (self.n_channels,))
        mean_features = _mean(features, (self.n_channels,))
        mean_waveforms = _mean(waveforms,
                               (self.n_samples_waveforms, self.n_channels))

        unmasked_channels = np.nonzero(mean_masks > .1)[0]
        n_unmasked_channels = len(unmasked_channels)
        # Weighted mean of the channels, weighted by the mean masks.
        mean_probe_position = _mean(self.model.probe.positions *
                                    mean_masks[:, np.newaxis], (2,))
        main_channels = np.argsort(mean_masks)[::-1]
        main_channels = np.array([c for c in main_channels
                                  if c in unmasked_channels])

        self.memory_store.store(cluster,
                                mean_masks=mean_masks,
                                mean_features=mean_features,
                                mean_waveforms=mean_waveforms,
                                mean_probe_position=mean_probe_position,
                                main_channels=main_channels,
                                n_unmasked_channels=n_unmasked_channels,
                                )

    def store(self, cluster, name=None):
        """Compute all statistics for one cluster."""
        if name is None:
            self.store_default(cluster)
            for func in self._funcs.values():
                func(cluster)
        else:
            assert name in self._funcs
            self._funcs[name](cluster)

    def empty_values(self, name):
        shape = {
            'mean_masks': (0, self.n_channels),
            'mean_features': (0, self.n_channels, self.n_features),
            'mean_waveforms': (0, self.n_samples_waveforms, self.n_channels),
            'mean_probe_position': (0, 2),

        }.get(name, (0,))
        # Default waveforms.
        return _default_array(shape, value=0. if name != 'mean_masks' else 1.)

    def load(self, cluster, name):
        return self.memory_store.load(cluster, name)

    def load_multi(self, clusters, name):
        return np.array([self.load(cluster, name)
                         for cluster in clusters], dtype=np.int64)

    def is_consistent(self, cluster, spikes):
        return cluster in self.memory_store


#------------------------------------------------------------------------------
# Store creation
#------------------------------------------------------------------------------

def create_store(model,
                 path=None,
                 spikes_per_cluster=None,
                 features_masks_chunk_size=100000,
                 waveforms_n_spikes_max=None,
                 waveforms_excerpt_size=None,
                 ):
    """Create a cluster store for a model."""
    cluster_store = ClusterStore(model=model,
                                 spikes_per_cluster=spikes_per_cluster,
                                 path=path,
                                 )

    # Create the FeatureMasks store item.
    # chunk_size is the number of spikes to load at once from
    # the features_masks array.
    cluster_store.register_item(FeatureMasks,
                                chunk_size=features_masks_chunk_size,
                                )
    cluster_store.register_item(Waveforms,
                                n_spikes_max=waveforms_n_spikes_max,
                                excerpt_size=waveforms_excerpt_size,
                                )
    cluster_store.register_item(ClusterStatistics)

    return cluster_store
