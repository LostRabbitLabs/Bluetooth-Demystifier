import React from 'react';
import PropTypes from 'prop-types';
import axios from 'axios';

import Button from '@mui/material/Button';
import Dialog from '@mui/material/Dialog';
import TextField from '@mui/material/TextField';
import DialogActions from '@mui/material/DialogActions';
import DialogContent from '@mui/material/DialogContent';
import DialogTitle from '@mui/material/DialogTitle';

export const TagDialog = ({
  mac,
  open,
  handleClose
}) => {
  const [tag, setTag] = React.useState('');

  /**
   * close
   * Wrap handleClose to clear the tag state
   *
   */
  const close = () => {
    setTag('');
    handleClose();
  }

  /**
   * addTag
   * POSTs a tag to the api for the mac
   *
   * @param {string} tag
   */
  const addTag = () => {
    axios.post(`/api/${mac}/tag`, { tag })
      .then((res) => {
        if (res.data) {
          close();
        } else {
          console.error(res);
        }
      }).catch((err) => {
        console.error(err);
      });
  };

  return (
    <Dialog
      open={open}
      onClose={handleClose}
    >
      <DialogTitle>
        Tag {mac}
      </DialogTitle>
      <DialogContent>
        <TextField
          value={tag}
          onChange={(e) => setTag(e.target.value)}
          autoFocus
          margin='dense'
          label='Tag'
          type='text'
          fullWidth
          variant='standard'
        />
        <DialogActions>
          <Button onClick={close}>Cancel</Button>
          <Button onClick={addTag}>Add Tag</Button>
        </DialogActions>
      </DialogContent>
    </Dialog>
  )
};

TagDialog.propTypes = {
  mac: PropTypes.string,
  open: PropTypes.bool,
  handleClose: PropTypes.func
};

export default TagDialog;
