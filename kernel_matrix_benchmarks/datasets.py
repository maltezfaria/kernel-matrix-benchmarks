import h5py
import numpy
import os
import random

from urllib.request import urlopen
from urllib.request import urlretrieve

from kernel_matrix_benchmarks.distance import dataset_transform


def download(src, dst):
    """Retrieves an online dataset, typically hosted on kernel-matrix-benchmarks.com."""

    if not os.path.exists(dst):
        # TODO: should be atomic
        print("downloading %s -> %s..." % (src, dst))
        urlretrieve(src, dst)


def get_dataset_fn(dataset):
    """Returns the name of the .hdf5 file for a given dataset."""
    if not os.path.exists("data"):
        os.mkdir("data")
    return os.path.join("data", "%s.hdf5" % dataset)


def get_dataset(which):
    """Returns a loaded .hdf5 file and the dimension of the points."""
    hdf5_fn = get_dataset_fn(which)

    # We first try to download the dataset from our website:
    try:
        # !!! replace by kernel-matrix-benchmarks.com once everything is ready
        url = "http://ann-benchmarks.com/%s.hdf5" % which
        download(url, hdf5_fn)

    # If this fails, we try to download it from an "original" repository
    # and process it as required:
    except:
        print("Cannot download %s" % url)
        if which in DATASETS:
            print("Creating dataset locally")
            DATASETS[which](hdf5_fn)
    hdf5_f = h5py.File(hdf5_fn, "r")

    # Here for backward compatibility, to ensure old datasets can still be used with newer versions:
    # cast to integer because the json parser (later on) cannot interpret numpy integers.
    dimension = (
        int(hdf5_f.attrs["dimension"])
        if "dimension" in hdf5_f.attrs
        else len(hdf5_f["train"][0])
    )

    return hdf5_f, dimension


# Everything below this line is related to creating datasets ===================
# You probably never need to do this at home,
# just rely on the prepared datasets at http://ann-benchmarks.com


# !!! Obsolete
def write_output(train, test, fn, distance, point_type="float", count=100):
    from kernel_matrix_benchmarks.algorithms.bruteforce import BruteForceBLAS

    n = 0
    f = h5py.File(fn, "w")
    f.attrs["type"] = "dense"
    f.attrs["distance"] = distance
    f.attrs["dimension"] = len(train[0])
    f.attrs["point_type"] = point_type
    print("train size: %9d * %4d" % train.shape)
    print("test size:  %9d * %4d" % test.shape)
    f.create_dataset("train", (len(train), len(train[0])), dtype=train.dtype)[:] = train
    f.create_dataset("test", (len(test), len(test[0])), dtype=test.dtype)[:] = test
    neighbors = f.create_dataset("neighbors", (len(test), count), dtype="i")
    distances = f.create_dataset("distances", (len(test), count), dtype="f")
    bf = BruteForceBLAS(distance, precision=train.dtype)

    bf.fit(train)
    for i, x in enumerate(test):
        if i % 1000 == 0:
            print("%d/%d..." % (i, len(test)))
        res = list(bf.query_with_distances(x, count))
        res.sort(key=lambda t: t[-1])
        neighbors[i] = [j for j, _ in res]
        distances[i] = [d for _, d in res]
    f.close()


"""
param: train and test are arrays of arrays of indices.
"""


# !!! Obsolete
def write_sparse_output(train, test, fn, distance, dimension, count=100):
    from kernel_matrix_benchmarks.algorithms.bruteforce import BruteForceBLAS

    f = h5py.File(fn, "w")
    f.attrs["type"] = "sparse"
    f.attrs["distance"] = distance
    f.attrs["dimension"] = dimension
    f.attrs["point_type"] = "bit"
    print("train size: %9d * %4d" % (train.shape[0], dimension))
    print("test size:  %9d * %4d" % (test.shape[0], dimension))

    # We ensure the sets are sorted
    train = numpy.array(list(map(sorted, train)))
    test = numpy.array(list(map(sorted, test)))

    flat_train = numpy.hstack(train.flatten())
    flat_test = numpy.hstack(test.flatten())

    f.create_dataset("train", (len(flat_train),), dtype=flat_train.dtype)[
        :
    ] = flat_train
    f.create_dataset("test", (len(flat_test),), dtype=flat_test.dtype)[:] = flat_test
    neighbors = f.create_dataset("neighbors", (len(test), count), dtype="i")
    distances = f.create_dataset("distances", (len(test), count), dtype="f")

    f.create_dataset("size_test", (len(test),), dtype="i")[:] = list(map(len, test))
    f.create_dataset("size_train", (len(train),), dtype="i")[:] = list(map(len, train))

    bf = BruteForceBLAS(distance, precision=train.dtype)
    bf.fit(train)
    for i, x in enumerate(test):
        if i % 1000 == 0:
            print("%d/%d..." % (i, len(test)))
        res = list(bf.query_with_distances(x, count))
        res.sort(key=lambda t: t[-1])
        neighbors[i] = [j for j, _ in res]
        distances[i] = [d for _, d in res]
    f.close()


def train_test_split(X, test_size=10000, dimension=None):
    import sklearn.model_selection

    if dimension == None:
        dimension = X.shape[1]
    print("Splitting %d*%d into train/test" % (X.shape[0], dimension))
    return sklearn.model_selection.train_test_split(
        X, test_size=test_size, random_state=1
    )


# GloVE 25, 50, 100 and 200 ----------------------------------------------------


def glove(out_fn, d):
    import zipfile

    url = "http://nlp.stanford.edu/data/glove.twitter.27B.zip"
    fn = os.path.join("data", "glove.twitter.27B.zip")
    download(url, fn)
    with zipfile.ZipFile(fn) as z:
        print("preparing %s" % out_fn)
        z_fn = "glove.twitter.27B.%dd.txt" % d
        X = []
        for line in z.open(z_fn):
            v = [float(x) for x in line.strip().split()[1:]]
            X.append(numpy.array(v))
        X_train, X_test = train_test_split(X)
        write_output(numpy.array(X_train), numpy.array(X_test), out_fn, "angular")


# MNIST and Fashion-MNIST ------------------------------------------------------


def _load_mnist_vectors(fn):
    import gzip
    import struct

    print("parsing vectors in %s..." % fn)
    f = gzip.open(fn)
    type_code_info = {
        0x08: (1, "!B"),
        0x09: (1, "!b"),
        0x0B: (2, "!H"),
        0x0C: (4, "!I"),
        0x0D: (4, "!f"),
        0x0E: (8, "!d"),
    }
    magic, type_code, dim_count = struct.unpack("!hBB", f.read(4))
    assert magic == 0
    assert type_code in type_code_info

    dimensions = [struct.unpack("!I", f.read(4))[0] for i in range(dim_count)]

    entry_count = dimensions[0]
    entry_size = numpy.product(dimensions[1:])

    b, format_string = type_code_info[type_code]
    vectors = []
    for i in range(entry_count):
        vectors.append(
            [struct.unpack(format_string, f.read(b))[0] for j in range(entry_size)]
        )
    return numpy.array(vectors)


def mnist(out_fn):
    download(
        "http://yann.lecun.com/exdb/mnist/train-images-idx3-ubyte.gz", "mnist-train.gz"
    )  # noqa
    download(
        "http://yann.lecun.com/exdb/mnist/t10k-images-idx3-ubyte.gz", "mnist-test.gz"
    )  # noqa
    train = _load_mnist_vectors("mnist-train.gz")
    test = _load_mnist_vectors("mnist-test.gz")
    write_output(train, test, out_fn, "euclidean")


def fashion_mnist(out_fn):
    download(
        "http://fashion-mnist.s3-website.eu-central-1.amazonaws.com/train-images-idx3-ubyte.gz",  # noqa
        "fashion-mnist-train.gz",
    )
    download(
        "http://fashion-mnist.s3-website.eu-central-1.amazonaws.com/t10k-images-idx3-ubyte.gz",  # noqa
        "fashion-mnist-test.gz",
    )
    train = _load_mnist_vectors("fashion-mnist-train.gz")
    test = _load_mnist_vectors("fashion-mnist-test.gz")
    write_output(train, test, out_fn, "euclidean")


# Full list of supported datasets ----------------------------------------------

DATASETS = {
    "mnist-784-euclidean": mnist,
    "fashion-mnist-784-euclidean": fashion_mnist,
    "glove-25-angular": lambda out_fn: glove(out_fn, 25),
    "glove-50-angular": lambda out_fn: glove(out_fn, 50),
    "glove-100-angular": lambda out_fn: glove(out_fn, 100),
    "glove-200-angular": lambda out_fn: glove(out_fn, 200),
}
