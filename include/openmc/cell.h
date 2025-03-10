#ifndef OPENMC_CELL_H
#define OPENMC_CELL_H

#include <cstdint>
#include <functional> // for hash
#include <limits>
#include <string>
#include <unordered_map>
#include <unordered_set>

#include "hdf5.h"
#include "pugixml.hpp"
#include <gsl/gsl-lite.hpp>

#include "openmc/constants.h"
#include "openmc/memory.h" // for unique_ptr
#include "openmc/neighbor_list.h"
#include "openmc/position.h"
#include "openmc/surface.h"
#include "openmc/universe.h"
#include "openmc/vector.h"

namespace openmc {

//==============================================================================
// Constants
//==============================================================================

enum class Fill { MATERIAL, UNIVERSE, LATTICE };

// TODO: Convert to enum
constexpr int32_t OP_LEFT_PAREN {std::numeric_limits<int32_t>::max()};
constexpr int32_t OP_RIGHT_PAREN {std::numeric_limits<int32_t>::max() - 1};
constexpr int32_t OP_COMPLEMENT {std::numeric_limits<int32_t>::max() - 2};
constexpr int32_t OP_INTERSECTION {std::numeric_limits<int32_t>::max() - 3};
constexpr int32_t OP_UNION {std::numeric_limits<int32_t>::max() - 4};

//==============================================================================
// Global variables
//==============================================================================

class Cell;
class ParentCell;
class CellInstance;
class Universe;
class UniversePartitioner;

namespace model {
extern std::unordered_map<int32_t, int32_t> cell_map;
extern vector<unique_ptr<Cell>> cells;

} // namespace model

//==============================================================================
//==============================================================================

class Cell {
public:
  //----------------------------------------------------------------------------
  // Constructors, destructors, factory functions

  explicit Cell(pugi::xml_node cell_node);
  Cell() {};
  virtual ~Cell() = default;

  //----------------------------------------------------------------------------
  // Methods

  //! \brief Determine if a cell contains the particle at a given location.
  //!
  //! The bounds of the cell are detemined by a logical expression involving
  //! surface half-spaces. At initialization, the expression was converted
  //! to RPN notation.
  //!
  //! The function is split into two cases, one for simple cells (those
  //! involving only the intersection of half-spaces) and one for complex cells.
  //! Simple cells can be evaluated with short circuit evaluation, i.e., as soon
  //! as we know that one half-space is not satisfied, we can exit. This
  //! provides a performance benefit for the common case. In
  //! contains_complex, we evaluate the RPN expression using a stack, similar to
  //! how a RPN calculator would work.
  //! \param r The 3D Cartesian coordinate to check.
  //! \param u A direction used to "break ties" the coordinates are very
  //!   close to a surface.
  //! \param on_surface The signed index of a surface that the coordinate is
  //!   known to be on.  This index takes precedence over surface sense
  //!   calculations.
  virtual bool contains(Position r, Direction u, int32_t on_surface) const = 0;

  //! Find the oncoming boundary of this cell.
  virtual std::pair<double, int32_t> distance(
    Position r, Direction u, int32_t on_surface, Particle* p) const = 0;

  //! Write all information needed to reconstruct the cell to an HDF5 group.
  //! \param group_id An HDF5 group id.
  void to_hdf5(hid_t group_id) const;

  virtual void to_hdf5_inner(hid_t group_id) const = 0;

  //! Export physical properties to HDF5
  //! \param[in] group  HDF5 group to read from
  void export_properties_hdf5(hid_t group) const;

  //! Import physical properties from HDF5
  //! \param[in] group  HDF5 group to write to
  void import_properties_hdf5(hid_t group);

  //! Get the BoundingBox for this cell.
  virtual BoundingBox bounding_box() const = 0;

  //----------------------------------------------------------------------------
  // Accessors

  //! Get the temperature of a cell instance
  //! \param[in] instance Instance index. If -1 is given, the temperature for
  //!   the first instance is returned.
  //! \return Temperature in [K]
  double temperature(int32_t instance = -1) const;

  //! Set the temperature of a cell instance
  //! \param[in] T Temperature in [K]
  //! \param[in] instance Instance index. If -1 is given, the temperature for
  //!   all instances is set.
  //! \param[in] set_contained If this cell is not filled with a material,
  //!   collect all contained cells with material fills and set their
  //!   temperatures.
  void set_temperature(
    double T, int32_t instance = -1, bool set_contained = false);

  //! Set the rotation matrix of a cell instance
  //! \param[in] rot The rotation matrix of length 3 or 9
  void set_rotation(const vector<double>& rot);

  //! Get the name of a cell
  //! \return Cell name
  const std::string& name() const { return name_; };

  //! Set the temperature of a cell instance
  //! \param[in] name Cell name
  void set_name(const std::string& name) { name_ = name; };

  //! Get all cell instances contained by this cell
  //! \param[in] instance Instance of the cell for which to get contained cells
  //! (default instance is zero)
  //! \param[in] hint positional hint for determining the parent cells
  //! \return Map with cell indexes as keys and
  //! instances as values
  std::unordered_map<int32_t, vector<int32_t>> get_contained_cells(
    int32_t instance = 0, Position* hint = nullptr) const;

protected:
  //! Determine the path to this cell instance in the geometry hierarchy
  //! \param[in] instance of the cell to find parent cells for
  //! \param[in] r position used to do a fast search for parent cells
  //! \return parent cells
  vector<ParentCell> find_parent_cells(
    int32_t instance, const Position& r) const;

  //! Determine the path to this cell instance in the geometry hierarchy
  //! \param[in] instance of the cell to find parent cells for
  //! \param[in] p particle used to do a fast search for parent cells
  //! \return parent cells
  vector<ParentCell> find_parent_cells(
    int32_t instance, Particle& p) const;

  //! Determine the path to this cell instance in the geometry hierarchy
  //! \param[in] instance of the cell to find parent cells for
  //! \return parent cells
  vector<ParentCell> exhaustive_find_parent_cells(
    int32_t instance) const;

  //! Inner function for retrieving contained cells
  void get_contained_cells_inner(
    std::unordered_map<int32_t, vector<int32_t>>& contained_cells,
    vector<ParentCell>& parent_cells) const;

public:
  //----------------------------------------------------------------------------
  // Data members

  int32_t id_;              //!< Unique ID
  std::string name_;        //!< User-defined name
  Fill type_;               //!< Material, universe, or lattice
  int32_t universe_;        //!< Universe # this cell is in
  int32_t fill_;            //!< Universe # filling this cell
  int32_t n_instances_ {0}; //!< Number of instances of this cell
  GeometryType geom_type_;  //!< Geometric representation type (CSG, DAGMC)

  //! \brief Index corresponding to this cell in distribcell arrays
  int distribcell_index_ {C_NONE};

  //! \brief Material(s) within this cell.
  //!
  //! May be multiple materials for distribcell.
  vector<int32_t> material_;

  //! \brief Temperature(s) within this cell.
  //!
  //! The stored values are actually sqrt(k_Boltzmann * T) for each temperature
  //! T. The units are sqrt(eV).
  vector<double> sqrtkT_;

  //! Definition of spatial region as Boolean expression of half-spaces
  vector<std::int32_t> region_;
  //! Reverse Polish notation for region expression
  vector<std::int32_t> rpn_;
  bool simple_; //!< Does the region contain only intersections?

  //! \brief Neighboring cells in the same universe.
  NeighborList neighbors_;

  Position translation_ {0, 0, 0}; //!< Translation vector for filled universe

  //! \brief Rotational tranfsormation of the filled universe.
  //
  //! The vector is empty if there is no rotation. Otherwise, the first 9 values
  //! give the rotation matrix in row-major order. When the user specifies
  //! rotation angles about the x-, y- and z- axes in degrees, these values are
  //! also present at the end of the vector, making it of length 12.
  vector<double> rotation_;

  vector<int32_t> offset_; //!< Distribcell offset table
};

struct CellInstanceItem {
  int32_t index {-1};    //! Index into global cells array
  int lattice_indx {-1}; //! Flat index value of the lattice cell
};

//==============================================================================

class CSGCell : public Cell {
public:
  CSGCell();

  explicit CSGCell(pugi::xml_node cell_node);

  bool contains(Position r, Direction u, int32_t on_surface) const override;

  std::pair<double, int32_t> distance(
    Position r, Direction u, int32_t on_surface, Particle* p) const override;

  void to_hdf5_inner(hid_t group_id) const override;

  BoundingBox bounding_box() const override;

protected:
  bool contains_simple(Position r, Direction u, int32_t on_surface) const;
  bool contains_complex(Position r, Direction u, int32_t on_surface) const;
  BoundingBox bounding_box_simple() const;
  static BoundingBox bounding_box_complex(vector<int32_t> rpn);

  //! Applies DeMorgan's laws to a section of the RPN
  //! \param start Starting point for token modification
  //! \param stop Stopping point for token modification
  static void apply_demorgan(
    vector<int32_t>::iterator start, vector<int32_t>::iterator stop);

  //! Removes complement operators from the RPN
  //! \param rpn The rpn to remove complement operators from.
  static void remove_complement_ops(vector<int32_t>& rpn);

  //! Returns the beginning position of a parenthesis block (immediately before
  //! two surface tokens) in the RPN given a starting position at the end of
  //! that block (immediately after two surface tokens)
  //! \param start Starting position of the search
  //! \param rpn The rpn being searched
  static vector<int32_t>::iterator find_left_parenthesis(
    vector<int32_t>::iterator start, const vector<int32_t>& rpn);
};

//==============================================================================
//! Define an instance of a particular cell
//==============================================================================

//!  Stores information used to identify a unique cell in the model
struct CellInstance {
  //! Check for equality
  bool operator==(const CellInstance& other) const
  {
    return index_cell == other.index_cell && instance == other.instance;
  }

  gsl::index index_cell;
  gsl::index instance;
};

//! Structure necessary for inserting CellInstance into hashed STL data
//! structures
struct CellInstanceHash {
  std::size_t operator()(const CellInstance& k) const
  {
    return 4096 * k.index_cell + k.instance;
  }
};

//==============================================================================
// Non-member functions
//==============================================================================

void read_cells(pugi::xml_node node);

//!  Add cells to universes
void populate_universes();

} // namespace openmc
#endif // OPENMC_CELL_H
